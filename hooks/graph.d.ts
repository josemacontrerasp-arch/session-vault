import type { VaultGraph, VaultNode, VaultView } from '../types'

export const ACCENT: string

export type SceneNode = {
  id: string
  kind: 'session' | 'tag'
  title: string
  project: string
  detail: string
  color: string
  r: number
  isSelf: boolean
  isFocus: boolean
}

export type SceneEdge = { a: string; b: string; k: string; w: number }

export type Scene = {
  nodes: SceneNode[]
  edges: SceneEdge[]
  isLocal: boolean
  centre: string
}

export type Drawing = {
  source: string
  alt: string
  sessions: number
  links: number
  scene: Scene
}

export function matches(node: VaultNode, query: string): boolean

export function buildScene(
  graph: VaultGraph,
  view: VaultView,
  self: string,
  limit?: number,
): Scene

export function drawGraph(
  graph: VaultGraph,
  view: VaultView,
  self: string,
  options?: {
    width?: number
    height?: number
    pixelWidth?: number
    labelUpTo?: number
    compact?: boolean
    limit?: number
  },
): Drawing
