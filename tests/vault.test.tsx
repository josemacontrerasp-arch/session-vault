// @ts-nocheck: the tests hand the kit only the fields of an event that matter here.
import { expect, mock, test } from 'claude-code/testing'

// The `on` a test body is handed: its hooks stand beneath the plugin.
type On = Parameters<Parameters<typeof test>[1]>[1]

const DESIGN = 'local_aaaaaaaa-0000-0000-0000-000000000001'
const DRYER = 'local_bbbbbbbb-0000-0000-0000-000000000002'
const NOTES = 'local_cccccccc-0000-0000-0000-000000000003'

const node = (id: string, title: string, project: string, cli: string, updated: number) => ({
  id,
  short: id.slice(6, 14),
  title,
  project,
  cwd: `/work/${project}`,
  created: 1,
  updated,
  prompts: 4,
  first: `the first prompt of ${title}`,
  last: 'thanks',
  note: `${title} (${id.slice(6, 14)})`,
  cli: [cli],
  archived: false,
})

const GRAPH = {
  v: 1,
  generatedAt: 4000,
  vault: '/vault',
  nodes: [
    node(DRYER, 'Dryer selection', 'PLANT', 'cli-b', 3000),
    node(DESIGN, 'Heat exchanger design', 'PLANT', 'cli-a', 2000),
    node(NOTES, 'Scratch notes', 'No folder', 'cli-c', 1000),
  ],
  edges: [{ a: DRYER, b: DESIGN, k: 'link', w: 1 }],
  mentions: [[NOTES, DESIGN]],
}

const PANE = {
  plugin: 'session-vault',
  component: 'Pane',
  requestId: 'session-vault',
  props: {
    title: 'Session vault',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

// Nothing sits beneath the plugin: these hooks stand for the engine and for
// the indexer, whose every run is recorded in `runs`.
type World = { live?: string; tasks?: unknown[]; sent?: unknown[]; stdin?: string[] }

function world(on: On, runs: string[][], options: World = {}) {
  on('session.send', (_, e) => {
    options.sent?.push(e)

    return { isDelivered: true as const }
  })
  on('ui.toast', () => ({ value: undefined }))
  mock.store(on)
  const clock = mock.clock(on, { now: 64_000 })
  on('command.register', () => ({ value: { command: 'vault' } }))
  on('tool.register', (_, e) => ({ value: { tool: `mcp__session-vault__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'cli-b' }))
  on('fs.read', () => ({ value: JSON.stringify(GRAPH) }))
  on('process.run', (_, e) => {
    const args = e.argv.slice(e.argv.findIndex(one => one.endsWith('vault.py')) + 1)
    runs.push(args)
    if (typeof e.init?.stdin === 'string') options.stdin?.push(e.init.stdin)
    const reply =
      args[0] === 'index'
        ? { ok: true, vault: '/vault', sessions: 3, edges: 1 }
        : args[0] === 'target'
          ? {
              ok: true,
              id: DESIGN,
              title: 'Heat exchanger design',
              live: options.live ?? '',
              sourceId: DRYER,
              sourceTitle: 'Dryer selection',
            }
          : args[0] === 'inbox'
            ? { ok: true, tasks: options.tasks ?? [] }
            : { ok: true, text: `ran ${args.join(' ')}` }

    return {
      value: {
        exitCode: 0,
        stdout: `${JSON.stringify(reply)}\n`,
        stderr: '',
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    }
  })

  return clock
}

test('the pane lists every session, on every surface', async ($, on) => {
  const runs: string[][] = []
  world(on, runs)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ran = await $.command.run({ command: 'vault', args: 'reindex' })
  expect(ran.text).toContain('3 sessions, 1 connections')

  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })

    expect((await ui.find({ type: 'Text', text: /3 sessions/ }))?.text).toContain('1 connections')
    expect(await ui.find({ key: 'pick' })).toBeDefined()
    const drawing = await ui.find({ type: 'Svg' })
    expect(drawing === undefined).toBe(surface === 'terminal')
    await ui.unmount()
  }
})

test('picking a session shows what it is doing and sends it what is typed', async ($, on) => {
  const runs: string[][] = []
  const stdin: string[] = []
  world(on, runs, { stdin })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'vault', args: 'reindex' })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.select({ key: 'pick', value: DESIGN })

  expect(await ui.find({ type: 'Text', text: /^Heat exchanger design$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /closed · PLANT/ })).toBeDefined()

  await ui.input({ key: 'message', text: 'recompute the duty' })
  expect(JSON.parse(stdin[0] ?? '{}')).toMatchObject({ to: DESIGN, text: 'recompute the duty' })
  await ui.unmount()
})

test('with no session picked, the message asks this session to search the vault', async ($, on) => {
  const runs: string[][] = []
  world(on, runs)
  const prompts: string[] = []
  on('prompt.submit', (_, e) => {
    prompts.push(e.text)

    return { text: e.text }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'vault', args: 'reindex' })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.input({ key: 'message', text: 'where did I size the dryer?' })

  expect(prompts.some(text => text.includes("The user's message: where did I size the dryer?"))).toBe(true)
  expect(prompts.some(text => text.includes('mcp__session-vault__search'))).toBe(true)
  expect(runs.some(args => args[0] === 'queue')).toBe(false)
  await ui.unmount()
})

test('a [[wikilink]] in a prompt tells the model which session it names', async ($, on) => {
  const runs: string[][] = []
  world(on, runs)
  const seen: (readonly string[] | undefined)[] = []
  on('prompt.submit', (_, e) => {
    seen.push(e.context)

    return { text: e.text }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'vault', args: 'reindex' })

  await $.prompt.submit({ text: 'what did I settle in [[heat exchanger]]?' })
  await $.prompt.submit({ text: 'matrix = [[1, 2], [3, 4]]' })

  expect(seen[0]?.[0]).toContain(DESIGN)
  expect(seen[0]?.[0]).toContain('mcp__session-vault__read')
  expect(seen[1]).toBeUndefined()
})

test('the search tool asks the indexer, leaving this session out', async ($, on) => {
  const runs: string[][] = []
  world(on, runs)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'vault', args: 'reindex' })

  await $.tool.call({ tool: 'mcp__session-vault__search', query: '--pinch analysis' })

  const search = runs.find(args => args[0] === 'search')
  expect(search).toEqual(['search', '--query=--pinch analysis', '--limit=8', `--exclude=${DRYER}`])
})

test('a task goes straight to a session that is running', async ($, on) => {
  const runs: string[][] = []
  const sent: unknown[] = []
  world(on, runs, { live: 'cli-a', sent })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'vault', args: 'reindex' })

  const ran = await $.tool.call({
    tool: 'mcp__session-vault__send',
    to: 'Heat exchanger design',
    task: 'recompute the duty for 40 C inlet',
  })

  expect(String(ran.result)).toContain('Delivered to "Heat exchanger design"')
  expect(sent).toHaveLength(1)
  expect(sent[0]).toMatchObject({
    to: expect.stringContaining('cli-a'),
    text: expect.stringContaining('recompute the duty for 40 C inlet'),
  })
  expect(runs.some(args => args[0] === 'queue')).toBe(false)
})

test('a task for a session that is not running waits in the inbox', async ($, on) => {
  const runs: string[][] = []
  const sent: unknown[] = []
  const stdin: string[] = []
  world(on, runs, { sent, stdin })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'vault', args: 'reindex' })

  const ran = await $.command.run({
    command: 'vault',
    args: 'send Heat exchanger design :: recompute the duty',
  })

  expect(ran.text).toContain('is in the vault')
  expect(sent).toHaveLength(0)
  expect(JSON.parse(stdin[0] ?? '{}')).toEqual({
    to: DESIGN,
    source: DRYER,
    sourceTitle: 'Dryer selection',
    text: 'recompute the duty',
  })
})

test('a waiting task is handed to its session as a prompt from the mod', async ($, on) => {
  const runs: string[][] = []
  const tasks = [{ id: 't1', at: 1, from: DESIGN, fromTitle: 'Heat exchanger design', text: 'size the dryer fan' }]
  const clock = world(on, runs, { tasks })
  const prompts: string[] = []
  on('prompt.submit', (_, e) => {
    prompts.push(e.text)

    return { text: e.text }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'vault', args: 'inbox' })
  await clock.advance(400)
  await clock.settle()

  expect(runs).toContainEqual(['inbox', '--session=cli-b', '--take'])
  expect(prompts.some(text => text.includes('size the dryer fan'))).toBe(true)
  expect(prompts.some(text => text.includes('Heat exchanger design'))).toBe(true)
})
