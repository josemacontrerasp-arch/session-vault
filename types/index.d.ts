export type VaultNode = {
  id: string
  short: string
  title: string
  project: string
  cwd: string
  created: number
  updated: number
  prompts: number
  first: string
  last: string
  note: string
  cli: string[]
  archived: boolean
  live?: boolean
  reply?: string
  lastRole?: string
}

export type VaultEdge = {
  a: string
  b: string
  k: 'link' | 'fork' | 'spawn' | 'files'
  w: number
}

export type VaultGraph = {
  v: number
  generatedAt: number
  vault: string
  nodes: VaultNode[]
  edges: VaultEdge[]
  mentions: string[][]
}

export type VaultView = {
  focus: string
  mode: 'global' | 'local'
  depth: number
  showTags: boolean
  showFiles: boolean
  showOrphans: boolean
  query: string
  page: number
}

export type VaultStatus = {
  phase: 'idle' | 'indexing' | 'ready' | 'error'
  message: string
  self: string
}

declare module 'claude-code' {
  interface PluginState {
    'session-vault': {
      graph: Shaped<VaultGraph>
      view: Shaped<VaultView>
      status: Shaped<VaultStatus>
    }
  }
}
