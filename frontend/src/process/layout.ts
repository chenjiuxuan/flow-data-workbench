import dagre from '@dagrejs/dagre'
import { PROCESS_NODE_META } from './meta'
import type { ProcessEdge, ProcessNode } from './types'

/** 图外部 ID 可包含 graphlib 对象保留键，布局只使用独立的安全内部 ID。 */
export function layoutProcess(nodes: ProcessNode[], edges: ProcessEdge[]) {
  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'LR', nodesep: 70, ranksep: 110, marginx: 50, marginy: 50, acyclicer: 'greedy' })
  graph.setDefaultEdgeLabel(() => ({}))
  const keys = new Map(nodes.map((node, index) => [node.id, `layout-node:${index}`]))
  for (const node of nodes) {
    const meta = PROCESS_NODE_META[node.type]
    graph.setNode(keys.get(node.id)!, { width: meta.width, height: meta.height })
  }
  for (const edge of edges) {
    const source = keys.get(edge.source), target = keys.get(edge.target)
    if (source && target) graph.setEdge(source, target)
  }
  dagre.layout(graph)
  return new Map(nodes.map((node) => {
    const position = graph.node(keys.get(node.id)!)
    const meta = PROCESS_NODE_META[node.type]
    return [node.id, { x: position.x - meta.width / 2, y: position.y - meta.height / 2 }] as const
  }))
}
