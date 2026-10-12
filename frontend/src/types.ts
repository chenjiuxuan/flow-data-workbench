/** 与后端 API 对应的数据结构。 */
export type NodeType = 'system' | 'database' | 'table' | 'api' | 'file' | 'external' | 'process_step'
export type ViewKey = 'system' | 'business' | 'field'

export interface GraphNode {
  id: string
  type: NodeType
  name: string
  attrs: Record<string, unknown>
  origin: 'auto' | 'manual'
  vanished: boolean
  x: number
  y: number
  manualFields: string[]
  provenance?: Record<string, unknown>
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  type: 'flow' | 'lineage' | 'uses' | string
  label: string
  attrs: Record<string, unknown>
  origin: 'auto' | 'manual'
  vanished: boolean
  manualFields: string[]
  provenance?: Record<string, unknown>
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/** 完整图备份，包含隐藏节点、所有视图与采集来源信息。 */
export interface GraphBackup extends GraphData {
  revision: string
}

export interface RestoreResult {
  revision: string
  [key: string]: number | string
}

export interface MergeReport {
  source: string
  added_nodes: string[]
  updated_nodes: string[]
  added_edges: string[]
  updated_edges: string[]
  vanished_nodes: string[]
  vanished_edges: string[]
  protected_fields: string[]
}

export interface SqlImportResult {
  tables: Record<string, string>
  edges: {
    source: string
    target: string
    sourceName: string
    targetName: string
    fields: string[]
    statements: string[]
  }[]
  errors: string[]
  warnings: string[]
  report: MergeReport | null
}

export interface DbDiscoverResult {
  collected: { database: number; tables: number }
  warnings: string[]
  report: MergeReport | null
}

export interface ExcelImportResult {
  parsed: { nodes: number; edges: number }
  errors: string[]
  report: MergeReport | null
}
