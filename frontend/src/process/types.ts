/** 流程文档与共享资产分别管理；节点和排版属于具体文档。 */
import type { GraphNode } from '../types'

export type ProcessNodeType = 'start' | 'task' | 'decision' | 'end'
export interface ProcessNode {
  id: string
  type: ProcessNodeType
  name: string
  x: number
  y: number
  owner: string
  description: string
  input: string
  output: string
  assetIds: string[]
}
export interface ProcessEdge {
  id: string
  source: string
  target: string
  label: string
  sourceHandle?: string | null
  targetHandle?: string | null
}
export interface ProcessContent { nodes: ProcessNode[]; edges: ProcessEdge[] }
export interface ProcessSummary {
  id: string
  name: string
  description: string
  revision: string
  nodeCount: number
  edgeCount: number
  updatedAt: string
}
export interface ProcessDiagram extends ProcessSummary, ProcessContent {}
export interface ProcessCreate extends ProcessContent { name: string; description: string }
export type ProcessAsset = GraphNode
export interface AssetUsage { id: string; name: string; nodeIds: string[] }
export interface ProcessBackup extends ProcessCreate { format: 'process-diagram/v1' }
