/** dagre 自动布局：从左到右分层，按主数据流方向排秩。

关键参数（调过几次）：
- rankdir='LR' + align='DL' + nodesep=120 + ranksep=200：
  把节点按主数据流方向拉开层级，层内节点上下分散，避免都挤在一条水平线上。
- 有向边按 type=flow/lineage 加权，让 dagre 按业务方向分层。
*/
import dagre from '@dagrejs/dagre'

interface LayoutInput {
  nodeIds: string[]
  edges: { source: string; target: string; type?: string }[]
}

const DEFAULT_NODE_W = 200
const DEFAULT_NODE_H = 70

export function autoLayout({ nodeIds, edges }: LayoutInput): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({
    rankdir: 'LR',
    align: 'DL',
    nodesep: 120,
    ranksep: 200,
    marginx: 60,
    marginy: 60,
    acyclicer: 'greedy',
    ranker: 'network-simplex',
  })
  g.setDefaultEdgeLabel(() => ({}))

  for (const id of nodeIds) g.setNode(id, { width: DEFAULT_NODE_W, height: DEFAULT_NODE_H })
  for (const e of edges) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) continue
    // flow/lineage 是主数据流方向，加权让 dagre 把它们排在 rank 上
    const weight = (e.type === 'flow' || e.type === 'lineage') ? 2 : 1
    g.setEdge(e.source, e.target, { weight })
  }
  dagre.layout(g)

  const positions: Record<string, { x: number; y: number }> = {}
  for (const id of nodeIds) {
    const node = g.node(id)
    if (node) positions[id] = { x: node.x - DEFAULT_NODE_W / 2, y: node.y - DEFAULT_NODE_H / 2 }
  }
  return positions
}
