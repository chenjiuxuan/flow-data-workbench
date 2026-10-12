/** 全局状态：图数据、视图、选中项、追溯结果、撤销重做。 */
import { temporal } from 'zundo'
import { create } from 'zustand'
import { api } from './api/client'
import type { GraphEdge, GraphNode, ViewKey } from './types'

export interface Selection {
  kind: 'node' | 'edge'
  id: string
}

interface TraceState {
  nodeId: string
  nodeName: string
  direction: 'upstream' | 'downstream' | 'both'
  nodeIds: Set<string>
  edgeIds: Set<string>
}

interface Store {
  view: ViewKey
  nodes: GraphNode[]
  edges: GraphEdge[]
  loading: boolean
  selection: Selection | null
  trace: TraceState | null

  setView: (view: ViewKey) => Promise<void>
  refresh: () => Promise<void>
  select: (sel: Selection | null) => void
  runTrace: (nodeId: string, direction: TraceState['direction']) => Promise<void>
  clearTrace: () => void
  selectedNode: () => GraphNode | null
  selectedEdge: () => GraphEdge | null
}

// 时间旅行只追踪 nodes/edges 的变化；selection/trace/loading/view 不影响撤销栈
const useStoreBase = create<Store>()(
  temporal(
    (set, get) => ({
      view: 'system' as ViewKey,
      nodes: [],
      edges: [],
      loading: false,
      selection: null,
      trace: null,

      setView: async (view) => {
        set({ view, loading: true, selection: null, trace: null })
        const data = await api.graph(view)
        set({ nodes: data.nodes, edges: data.edges, loading: false })
      },

      refresh: async () => {
        const data = await api.graph(get().view)
        set({ nodes: data.nodes, edges: data.edges })
      },

      select: (selection) => set({ selection }),

      runTrace: async (nodeId, direction) => {
        const data = await api.trace(nodeId, direction)
        set({
          trace: {
            nodeId,
            nodeName: data.nodes.find((n) => n.id === nodeId)?.name ?? nodeId,
            direction,
            nodeIds: new Set(data.nodes.map((n) => n.id)),
            edgeIds: new Set(data.edges.map((e) => e.id)),
          },
        })
      },

      clearTrace: () => set({ trace: null }),

      selectedNode: () => {
        const sel = get().selection
        if (!sel || sel.kind !== 'node') return null
        return get().nodes.find((n) => n.id === sel.id) ?? null
      },

      selectedEdge: () => {
        const sel = get().selection
        if (!sel || sel.kind !== 'edge') return null
        return get().edges.find((e) => e.id === sel.id) ?? null
      },
    }),
    {
      partialize: (s) => ({ nodes: s.nodes, edges: s.edges }),
      limit: 200,
    },
  ),
)

export const useStore = useStoreBase
