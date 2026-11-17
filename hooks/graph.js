// The graph view, pure: which sessions and connections show, where each node
// sits, and the SVG that draws them. No `$` here, so the same file runs in a
// browser for a look at the drawing (preview/index.html).
//
// As in Obsidian's graph view: a note (here a session) is a node, a link is a
// line, a node grows with the number of connections it has, tags (here the
// project folder) can be shown as nodes of their own, and the local graph is
// the neighbourhood of one note out to a chosen depth.

export const ACCENT = '#a882ff'
const GRAY = '#8b8f98'
const PALETTE = [
  '#5b9bd5',
  '#e0965c',
  '#6dbb7a',
  '#d46a8c',
  '#c9b458',
  '#56b6c2',
  '#e06c75',
  '#9d8fd6',
  '#a3be8c',
  '#d19a66',
  '#7aa2f7',
  '#bb9af7',
]
const MAX_SVG = 120000

function hash(text) {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function unit(text) {
  return hash(text) / 4294967296
}

function xml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, ' ')
}

function shorten(text, limit) {
  const flat = String(text).replace(/\s+/g, ' ').trim()
  return flat.length <= limit ? flat : `${flat.slice(0, limit - 1).trimEnd()}…`
}

export function matches(node, query) {
  const want = query.trim().toLowerCase()
  if (want === '') return true
  const hay = `${node.title}\n${node.project}\n${node.first}\n${node.last}`
  return hay.toLowerCase().includes(want)
}

/**
 * What the view shows: session nodes, tag nodes and the edges between them.
 * `self` is the id of the session the pane is drawn in ('' when unknown).
 */
export function buildScene(graph, view, self, limit = 160) {
  const byId = new Map(graph.nodes.map(node => [node.id, node]))
  const focus = byId.has(view.focus) ? view.focus : ''
  const centre = focus || (byId.has(self) ? self : '')
  const isLocal = view.mode === 'local' && centre !== ''

  let sessions = graph.nodes.filter(node => matches(node, view.query))
  if (sessions.length > limit) {
    const kept = sessions.slice(0, limit)
    for (const id of [focus, self]) {
      const node = byId.get(id)
      if (node && !kept.includes(node) && sessions.includes(node)) kept.push(node)
    }
    sessions = kept
  }
  let shown = new Set(sessions.map(node => node.id))

  let edges = graph.edges.filter(
    edge =>
      shown.has(edge.a) &&
      shown.has(edge.b) &&
      (edge.k !== 'files' || view.showFiles),
  )

  const tagOf = node => `#${node.project}`
  let tagEdges = []
  if (view.showTags) {
    tagEdges = sessions.map(node => ({ a: node.id, b: tagOf(node), k: 'tag', w: 1 }))
  }

  const around = new Map()
  const join = (a, b) => {
    if (!around.has(a)) around.set(a, new Set())
    if (!around.has(b)) around.set(b, new Set())
    around.get(a).add(b)
    around.get(b).add(a)
  }
  for (const edge of edges) join(edge.a, edge.b)
  for (const edge of tagEdges) join(edge.a, edge.b)

  if (isLocal) {
    const reach = new Set([centre])
    let rim = [centre]
    for (let depth = 0; depth < view.depth; depth++) {
      const next = []
      for (const id of rim) {
        for (const other of around.get(id) ?? []) {
          if (!reach.has(other)) {
            reach.add(other)
            next.push(other)
          }
        }
      }
      rim = next
    }
    shown = new Set([...shown].filter(id => reach.has(id)))
    if (byId.has(centre) && matches(byId.get(centre), view.query)) shown.add(centre)
  } else if (!view.showOrphans) {
    shown = new Set([...shown].filter(id => (around.get(id)?.size ?? 0) > 0 || id === self))
  }

  sessions = sessions.filter(node => shown.has(node.id))
  edges = edges.filter(edge => shown.has(edge.a) && shown.has(edge.b))
  tagEdges = tagEdges.filter(edge => shown.has(edge.a))

  const projects = new Map()
  for (const node of graph.nodes) {
    projects.set(node.project, (projects.get(node.project) ?? 0) + 1)
  }
  const ranked = [...projects.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
  const colorOf = new Map(ranked.map(([name], index) => [name, PALETTE[index % PALETTE.length]]))

  const degree = new Map()
  for (const edge of edges) {
    degree.set(edge.a, (degree.get(edge.a) ?? 0) + 1)
    degree.set(edge.b, (degree.get(edge.b) ?? 0) + 1)
  }

  const nodes = sessions.map(node => ({
    id: node.id,
    kind: 'session',
    title: node.title,
    project: node.project,
    detail: `${node.project} · ${node.prompts} prompt${node.prompts === 1 ? '' : 's'}`,
    color: colorOf.get(node.project) ?? GRAY,
    r: Math.min(13, 4 + 1.9 * Math.sqrt(degree.get(node.id) ?? 0)),
    isSelf: node.id === self,
    isFocus: node.id === focus,
  }))

  const members = new Map()
  for (const edge of tagEdges) members.set(edge.b, (members.get(edge.b) ?? 0) + 1)
  for (const [id, count] of members) {
    const project = id.slice(1)
    nodes.push({
      id,
      kind: 'tag',
      title: id,
      project,
      detail: `${count} session${count === 1 ? '' : 's'}`,
      count,
      color: colorOf.get(project) ?? GRAY,
      r: Math.min(9, 2.5 + 0.9 * Math.sqrt(count)),
      isSelf: false,
      isFocus: false,
    })
  }

  return { nodes, edges: [...edges, ...tagEdges], isLocal, centre }
}

const PULL = { link: 1, fork: 1, spawn: 1, files: 0.35, tag: 0.55 }
const GRAVITY = 3

/** A force-directed layout, the same for the same scene; positions fit W x H. */
export function layout(scene, width, height, pad = 34) {
  const { nodes, edges } = scene
  const count = nodes.length
  const at = new Map()
  if (count === 0) return at
  const index = new Map(nodes.map((node, i) => [node.id, i]))
  const xs = new Float64Array(count)
  const ys = new Float64Array(count)

  const groups = [...new Set(nodes.map(node => node.project))]
  const spread = Math.sqrt(count) * 16
  nodes.forEach((node, i) => {
    const turn = (groups.indexOf(node.project) / Math.max(1, groups.length)) * Math.PI * 2
    const cx = groups.length > 1 ? Math.cos(turn) * spread : 0
    const cy = groups.length > 1 ? Math.sin(turn) * spread : 0
    const out = node.kind === 'tag' ? 0 : 12 + unit(`${node.id}r`) * spread * 0.45
    const angle = unit(`${node.id}a`) * Math.PI * 2
    xs[i] = cx + Math.cos(angle) * out
    ys[i] = cy + Math.sin(angle) * out
  })

  const links = edges
    .map(edge => [index.get(edge.a), index.get(edge.b), PULL[edge.k] ?? 0.5])
    .filter(link => link[0] !== undefined && link[1] !== undefined)

  const k = 42
  const rounds = Math.max(40, Math.min(280, Math.floor(2400000 / (count * count + 1))))
  // A weaker pull along the longer side lets the layout fill the frame.
  const flat = Math.min(1, (height / width) ** 2 * 1.6)
  const tall = Math.min(1, (width / height) ** 2 * 1.6)
  const dx = new Float64Array(count)
  const dy = new Float64Array(count)
  for (let round = 0; round < rounds; round++) {
    dx.fill(0)
    dy.fill(0)
    for (let i = 0; i < count; i++) {
      for (let j = i + 1; j < count; j++) {
        let ux = xs[i] - xs[j]
        let uy = ys[i] - ys[j]
        let d2 = ux * ux + uy * uy
        if (d2 < 0.01) {
          ux = unit(`${i}:${j}`) - 0.5
          uy = unit(`${j}:${i}`) - 0.5
          d2 = ux * ux + uy * uy + 0.01
        }
        const push = (k * k) / d2
        dx[i] += ux * push
        dy[i] += uy * push
        dx[j] -= ux * push
        dy[j] -= uy * push
      }
    }
    for (const [a, b, strength] of links) {
      const ux = xs[a] - xs[b]
      const uy = ys[a] - ys[b]
      const d = Math.sqrt(ux * ux + uy * uy) + 0.01
      const pull = (d / k) * strength
      dx[a] -= ux * pull
      dy[a] -= uy * pull
      dx[b] += ux * pull
      dy[b] += uy * pull
    }
    const heat = k * 2.2 * (1 - round / rounds) + 0.4
    for (let i = 0; i < count; i++) {
      dx[i] -= xs[i] * GRAVITY * flat
      dy[i] -= ys[i] * GRAVITY * tall
      const move = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) + 0.0001
      const step = Math.min(move, heat)
      xs[i] += (dx[i] / move) * step
      ys[i] += (dy[i] / move) * step
    }
  }

  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (let i = 0; i < count; i++) {
    minX = Math.min(minX, xs[i])
    maxX = Math.max(maxX, xs[i])
    minY = Math.min(minY, ys[i])
    maxY = Math.max(maxY, ys[i])
  }
  const spanX = Math.max(1, maxX - minX)
  const spanY = Math.max(1, maxY - minY)
  // Labels are centred under their nodes, so the sides need more room.
  const side = pad * 1.5
  const scale = Math.min((width - side * 2) / spanX, (height - pad * 2) / spanY, 2.4)
  const offX = (width - spanX * scale) / 2
  const offY = (height - spanY * scale) / 2
  nodes.forEach((node, i) => {
    at.set(node.id, [
      count === 1 ? width / 2 : offX + (xs[i] - minX) * scale,
      count === 1 ? height / 2 : offY + (ys[i] - minY) * scale,
    ])
  })
  return at
}

function round(value) {
  return Math.round(value * 10) / 10
}

function draw(scene, at, width, height, options) {
  const { nodes, edges } = scene
  const index = new Map(nodes.map((node, i) => [node.id, i]))
  const near = nodes.map(() => [])
  for (const edge of edges) {
    const a = index.get(edge.a)
    const b = index.get(edge.b)
    if (a === undefined || b === undefined) continue
    near[a].push(b)
    near[b].push(a)
  }
  const sessionCount = nodes.filter(node => node.kind === 'session').length
  const labelAll = scene.isLocal ? nodes.length <= 70 : sessionCount <= options.labelUpTo
  const size = options.compact ? 9 : 11
  // Drawn wider than any pane, so the surface scales it down to the pane's
  // width and never leaves it small in a corner.
  const pixels = options.pixelWidth ?? width
  const pixelsTall = Math.round((pixels * height) / width)

  const out = []
  out.push(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${pixels}" height="${pixelsTall}" font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif" font-size="${size}">`,
  )
  if (options.hover) {
    const css = [
      '.n text.q{transition:opacity .12s}',
      '.n,.e{transition:opacity .12s}',
      'svg:has(.n:hover) .n{opacity:.18}',
      'svg:has(.n:hover) .e{opacity:.06}',
    ]
    nodes.forEach((node, i) => {
      css.push(`svg:has(.i${i}:hover) .b${i}{opacity:1}`)
      css.push(`svg:has(.i${i}:hover) .e${i}{opacity:.95;stroke:${ACCENT}}`)
    })
    css.push('svg .n:hover{opacity:1}')
    css.push('svg .n:hover text.q,svg:has(.n:hover) .n text.q{opacity:1}')
    css.push('svg:has(.n:hover) .n:not(:hover) text{opacity:.9}')
    out.push(`<style>${css.join('')}</style>`)
  }
  out.push(
    `<defs><marker id="tip" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0L8,4L0,8z" fill="${ACCENT}"/></marker></defs>`,
  )

  out.push('<g fill="none" stroke-linecap="round">')
  for (const edge of edges) {
    const a = index.get(edge.a)
    const b = index.get(edge.b)
    const from = at.get(edge.a)
    const to = at.get(edge.b)
    if (a === undefined || b === undefined || !from || !to) continue
    let [x2, y2] = to
    let look = `stroke="${GRAY}" stroke-width="1" opacity=".5"`
    if (edge.k === 'link') {
      const ux = to[0] - from[0]
      const uy = to[1] - from[1]
      const d = Math.sqrt(ux * ux + uy * uy) || 1
      const back = Math.min(d / 2, nodes[b].r + 2)
      x2 = to[0] - (ux / d) * back
      y2 = to[1] - (uy / d) * back
      look = `stroke="${ACCENT}" stroke-width="1.4" opacity=".85" marker-end="url(#tip)"`
    } else if (edge.k === 'files') {
      look = `stroke="${GRAY}" stroke-width=".9" stroke-dasharray="3 3" opacity=".5"`
    } else if (edge.k === 'tag') {
      look = `stroke="${nodes[b].color}" stroke-width=".7" opacity=".38"`
    }
    out.push(
      `<line class="e e${a} e${b}" x1="${round(from[0])}" y1="${round(from[1])}" x2="${round(x2)}" y2="${round(y2)}" ${look}/>`,
    )
  }
  out.push('</g>')

  out.push(`<g text-anchor="middle" fill="${GRAY}">`)
  nodes.forEach((node, i) => {
    const spot = at.get(node.id)
    if (!spot) return
    const classes = ['n', `i${i}`, ...near[i].map(other => `b${other}`)].join(' ')
    const parts = [`<g class="${classes}" transform="translate(${round(spot[0])},${round(spot[1])})">`]
    parts.push(`<title>${xml(node.title)} — ${xml(node.detail)}</title>`)
    parts.push(`<circle r="${round(node.r + 7)}" fill="${GRAY}" opacity="0"/>`)
    if (node.kind === 'tag') {
      parts.push(
        `<circle r="${round(node.r)}" fill="${node.color}" fill-opacity=".16" stroke="${node.color}" stroke-width="1.3"/>`,
      )
    } else {
      if (node.isSelf || node.isFocus) {
        parts.push(
          `<circle r="${round(node.r + 3.5)}" fill="none" stroke="${ACCENT}" stroke-width="${node.isFocus ? 2 : 1.3}"${node.isFocus ? '' : ' stroke-dasharray="2.5 2"'}/>`,
        )
      }
      parts.push(`<circle r="${round(node.r)}" fill="${node.color}"/>`)
    }
    const isNamedTag = node.kind === 'tag' && (!options.compact || node.count >= 4)
    const always = labelAll || isNamedTag || node.isSelf || node.isFocus
    if (always || options.hover) {
      const text = node.kind === 'tag' ? shorten(node.title, 18) : shorten(node.title, options.compact ? 16 : 22)
      const weight = node.kind === 'tag' || node.isSelf || node.isFocus ? ' font-weight="600"' : ''
      const fill = node.kind === 'tag' ? ` fill="${node.color}"` : node.isSelf || node.isFocus ? ` fill="${ACCENT}"` : ''
      const quiet = always ? '' : ' class="q" opacity="0"'
      parts.push(`<text y="${round(node.r + size + 2)}"${weight}${fill}${quiet}>${xml(text)}</text>`)
    }
    parts.push('</g>')
    out.push(parts.join(''))
  })
  out.push('</g></svg>')
  return out.join('')
}

/**
 * The scene as an SVG document, kept under the element's size limit: the
 * hover styles go first when it runs long, then the newest sessions are kept.
 */
export function drawGraph(graph, view, self, options = {}) {
  const width = options.width ?? 420
  const full = options.height ?? 400
  const compact = options.compact === true
  let limit = options.limit ?? 160
  for (;;) {
    const scene = buildScene(graph, view, self, limit)
    // A handful of nodes needs no tall frame.
    const height = scene.nodes.length <= 14 ? Math.min(full, Math.round(width * 0.72)) : full
    const at = layout(scene, width, height, compact ? 18 : 38)
    const sessions = scene.nodes.filter(node => node.kind === 'session').length
    const links = scene.edges.filter(edge => edge.k !== 'tag').length
    const alt =
      sessions === 0
        ? 'An empty graph: no session matches.'
        : `A graph of ${sessions} session${sessions === 1 ? '' : 's'} and ${links} connection${links === 1 ? '' : 's'}${scene.isLocal ? ' around one session' : ''}.`
    for (const hover of compact ? [false] : [true, false]) {
      const source = draw(scene, at, width, height, {
        hover,
        compact,
        labelUpTo: compact ? 0 : 24,
        pixelWidth: options.pixelWidth,
      })
      if (source.length <= MAX_SVG) return { source, alt, sessions, links, scene }
    }
    if (limit <= 20) return { source: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} 20" width="${width}" height="20"/>`, alt, sessions, links, scene }
    limit = Math.floor(limit / 2)
  }
}
