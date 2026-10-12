/** 图查询与临时选择独立于编辑历史；所有写操作在同一队列中执行。 */
import { create } from 'zustand'
import { api } from './api/client'
import { autoLayout } from './utils/layout'
import { nodeBounds } from './utils/bounds'
import type { GraphBackup, GraphData, GraphEdge, GraphNode, ViewKey } from './types'

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

interface HistoryEntry {
  before: GraphBackup
  after: GraphBackup
}

interface ExternalOptions<T> {
  view?: (result: T) => ViewKey | undefined
  applied?: (result: T) => boolean
  layout?: boolean
}

interface Store {
  view: ViewKey
  nodes: GraphNode[]
  edges: GraphEdge[]
  loading: boolean
  busy: boolean
  fitVersion: number
  selection: Selection | null
  canvasSelection: { nodes: string[]; edges: string[] }
  trace: TraceState | null
  past: HistoryEntry[]
  future: HistoryEntry[]
  historyRevision: string | null

  initialize: () => Promise<void>
  setView: (view: ViewKey) => Promise<void>
  refresh: () => Promise<void>
  select: (sel: Selection | null) => void
  setCanvasSelection: (nodes: string[], edges: string[]) => void
  runTrace: (nodeId: string, direction: TraceState['direction']) => Promise<void>
  clearTrace: () => void
  selectedNode: () => GraphNode | null
  selectedEdge: () => GraphEdge | null

  createNode: (data: { type: GraphNode['type']; name: string; x: number; y: number }) => Promise<boolean>
  createEdge: (data: { source: string; target: string; type: string; label: string }) => Promise<boolean>
  patchNode: (id: string, data: { name: string; attrs: Record<string, unknown> }) => Promise<boolean>
  patchEdge: (id: string, data: { label: string; attrs: Record<string, unknown> }) => Promise<boolean>
  deleteItems: (nodeIds: string[], edgeIds?: string[]) => Promise<boolean>
  saveLayout: (positions: Record<string, { x: number; y: number }>) => Promise<boolean>
  undo: () => Promise<boolean>
  redo: () => Promise<boolean>
  runExternal: <T>(operation: () => Promise<T>, options?: ExternalOptions<T>) => Promise<T>
}

const VIEW_TYPES: Record<ViewKey, GraphNode['type'][]> = {
  system: ['system', 'database', 'api', 'file', 'external'],
  business: ['process_step', 'system'],
  field: ['table'],
}

/** 成功写入后立即按当前视图投影，避免用旧视图的响应更新画布。 */
export function projectBackup(backup: GraphBackup, view: ViewKey): GraphData {
  const nodes = backup.nodes.filter((n) => !n.vanished && VIEW_TYPES[view].includes(n.type))
  const ids = new Set(nodes.map((n) => n.id))
  return { nodes, edges: backup.edges.filter((e) => !e.vanished && ids.has(e.source) && ids.has(e.target)) }
}

const sameGraph = (a: GraphBackup, b: GraphBackup) =>
  JSON.stringify([a.nodes, a.edges]) === JSON.stringify([b.nodes, b.edges])

/** 保留所有来源状态；null 清空属性，并保护实际改过的字段。 */
function patchRecord<T extends GraphNode | GraphEdge>(record: T, nameKey: 'name' | 'label', name: string, attrs: Record<string, unknown>) {
  const changed: string[] = []
  if ((record as unknown as Record<string, unknown>)[nameKey] !== name) {
    Object.assign(record, { [nameKey]: name })
    changed.push(nameKey)
  }
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null) {
      if (key in record.attrs) { delete record.attrs[key]; changed.push(key) }
    } else {
      if (JSON.stringify(record.attrs[key]) !== JSON.stringify(value)) changed.push(key)
      record.attrs[key] = value
    }
  }
  if (changed.length) {
    const provenance = record.provenance ?? {}
    const manualFields = new Set([
      ...record.manualFields,
      ...(Array.isArray(provenance.manual_fields) ? provenance.manual_fields as string[] : []),
      ...changed,
    ])
    record.manualFields = [...manualFields].sort()
    record.provenance = { ...provenance, manual_fields: record.manualFields }
  }
}

/** 仅在初始化或导入后安排无坐标节点；普通查询不会隐式写布局或产生历史。 */
function arrangeUnpositioned(backup: GraphBackup): GraphBackup {
  const next = structuredClone(backup)
  const groups = [
    next.nodes.filter((n) => !n.vanished && VIEW_TYPES.system.includes(n.type)),
    next.nodes.filter((n) => !n.vanished && n.type === 'process_step'),
    next.nodes.filter((n) => !n.vanished && n.type === 'table'),
  ]
  for (const nodes of groups) {
    const piled = nodes.filter((n) => n.x === 0 && n.y === 0)
    if (!piled.length || nodes.length < 2) continue
    if (piled.length === nodes.length) {
      const ids = new Set(nodes.map((n) => n.id))
      const positions = autoLayout({ nodeIds: [...ids], edges: next.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) })
      for (const node of nodes) Object.assign(node, positions[node.id])
    } else {
      const maxX = nodeBounds(nodes)!.maxX
      piled.forEach((node, i) => { node.x = maxX + 320; node.y = 80 + i * 110 })
    }
  }
  return next
}

export function createGraphStore() {
  let graphRequest = 0
  let traceRequest = 0
  let pending = 0
  let queue: Promise<unknown> = Promise.resolve()

  return create<Store>((set, get) => {
    const enqueue = <T,>(operation: () => Promise<T>): Promise<T> => {
      pending += 1
      set({ busy: true })
      const result = queue.then(operation)
      queue = result.catch(() => undefined)
      return result.finally(() => {
        pending -= 1
        set({ busy: pending > 0 })
      })
    }

    const applyGraph = (data: GraphData) => {
      const state = get()
      const nodeIds = new Set(data.nodes.map((n) => n.id))
      const edgeIds = new Set(data.edges.map((e) => e.id))
      const selection = state.selection
      set({
        ...data, loading: false,
        selection: selection && (selection.kind === 'node' ? nodeIds : edgeIds).has(selection.id) ? selection : null,
        canvasSelection: {
          nodes: state.canvasSelection.nodes.filter((id) => nodeIds.has(id)),
          edges: state.canvasSelection.edges.filter((id) => edgeIds.has(id)),
        },
      })
    }

    const publish = (backup: GraphBackup) => {
      graphRequest += 1
      traceRequest += 1
      set({ trace: null })
      applyGraph(projectBackup(backup, get().view))
    }

    const restore = async (snapshot: GraphBackup, expectedRevision: string) => {
      const result = await api.restoreJson({ ...snapshot, mode: 'replace', expected_revision: expectedRevision })
      return { ...snapshot, revision: result.revision }
    }

    const edit = (change: (backup: GraphBackup) => void) => enqueue(async () => {
      const before = await api.exportJson()
      const desired = structuredClone(before)
      change(desired)
      if (sameGraph(before, desired)) return false
      // 全图 CAS 提交使多选删除、属性和布局均为单次原子编辑。
      const after = await restore(desired, before.revision)
      const state = get()
      const past = state.historyRevision === before.revision ? state.past : []
      set({ past: [...past, { before, after }].slice(-200), future: [], historyRevision: after.revision })
      publish(after)
      return true
    })

    const refresh = async () => {
      const view = get().view
      const request = ++graphRequest
      set({ loading: true })
      try {
        const data = await api.graph(view)
        if (request === graphRequest && view === get().view) applyGraph(data)
      } catch (error) {
        if (request === graphRequest) { set({ loading: false }); throw error }
      }
    }

    const travel = (direction: 'undo' | 'redo') => enqueue(async () => {
      const state = get()
      const source = direction === 'undo' ? state.past : state.future
      const entry = source.at(-1)
      if (!entry || !state.historyRevision) return false
      const desired = direction === 'undo' ? entry.before : entry.after
      // 失败时完全保留两侧历史；外部变更不能被旧快照覆盖。
      const restored = await restore(desired, state.historyRevision)
      set(direction === 'undo'
        ? { past: state.past.slice(0, -1), future: [...state.future, entry], historyRevision: restored.revision }
        : { past: [...state.past, entry], future: state.future.slice(0, -1), historyRevision: restored.revision })
      publish(restored)
      return true
    })

    return {
      view: 'system', nodes: [], edges: [], loading: false, busy: false, fitVersion: 0,
      selection: null, canvasSelection: { nodes: [], edges: [] }, trace: null,
      past: [], future: [], historyRevision: null,

      initialize: () => enqueue(async () => {
        const before = await api.exportJson()
        const arranged = arrangeUnpositioned(before)
        set({ fitVersion: get().fitVersion + 1 })
        publish(sameGraph(before, arranged) ? before : await restore(arranged, before.revision))
      }),
      setView: async (view) => {
        traceRequest += 1
        set({ view, nodes: [], edges: [], selection: null, canvasSelection: { nodes: [], edges: [] }, trace: null })
        await refresh()
      },
      refresh,
      select: (selection) => set({ selection }),
      setCanvasSelection: (nodes, edges) => {
        const current = get().canvasSelection
        if (JSON.stringify([nodes, edges]) === JSON.stringify([current.nodes, current.edges])) return
        // 框选、pointerdown 和拖动仅更新画布选择；明确点击才打开属性面板。
        const selected = get().selection
        const selection = selected && (selected.kind === 'node' ? nodes : edges).includes(selected.id) ? selected : null
        set({ canvasSelection: { nodes, edges }, selection })
      },
      runTrace: async (nodeId, direction) => {
        const request = ++traceRequest
        const view = get().view
        const data = await api.trace(nodeId, direction)
        if (request !== traceRequest || view !== get().view) return
        set({ trace: {
          nodeId, nodeName: data.nodes.find((n) => n.id === nodeId)?.name ?? nodeId, direction,
          nodeIds: new Set(data.nodes.map((n) => n.id)), edgeIds: new Set(data.edges.map((e) => e.id)),
        } })
      },
      clearTrace: () => { traceRequest += 1; set({ trace: null }) },
      selectedNode: () => get().selection?.kind === 'node' ? get().nodes.find((n) => n.id === get().selection?.id) ?? null : null,
      selectedEdge: () => get().selection?.kind === 'edge' ? get().edges.find((e) => e.id === get().selection?.id) ?? null : null,

      createNode: (data) => edit((backup) => {
        if (!data.name.trim()) throw new Error('名称不能为空')
        backup.nodes.push({ ...data, name: data.name.trim(), id: `manual:${crypto.randomUUID()}`,
          attrs: {}, origin: 'manual', vanished: false, manualFields: [], provenance: {} })
      }),
      createEdge: (data) => edit((backup) => {
        if (data.source === data.target) throw new Error('不能连接到自身')
        if (![data.source, data.target].every((id) => backup.nodes.some((n) => n.id === id))) throw new Error('连线端点不存在')
        if (backup.edges.some((e) => e.source === data.source && e.target === data.target && e.type === data.type && e.label === data.label)) throw new Error('该连线已存在')
        backup.edges.push({ ...data, id: `manual-edge:${crypto.randomUUID()}`, attrs: {},
          origin: 'manual', vanished: false, manualFields: [], provenance: {} })
      }),
      patchNode: (id, data) => edit((backup) => {
        const node = backup.nodes.find((n) => n.id === id)
        if (!node) throw new Error('节点不存在')
        if (!data.name.trim()) throw new Error('名称不能为空')
        patchRecord(node, 'name', data.name.trim(), data.attrs)
      }),
      patchEdge: (id, data) => edit((backup) => {
        const edge = backup.edges.find((e) => e.id === id)
        if (!edge) throw new Error('连线不存在')
        patchRecord(edge, 'label', data.label.trim(), data.attrs)
      }),
      deleteItems: (nodeIds, edgeIds = []) => edit((backup) => {
        const nodes = new Set(nodeIds)
        const edges = new Set(edgeIds)
        backup.nodes = backup.nodes.filter((n) => !nodes.has(n.id))
        backup.edges = backup.edges.filter((e) => !edges.has(e.id) && !nodes.has(e.source) && !nodes.has(e.target))
      }),
      saveLayout: (positions) => edit((backup) => {
        for (const node of backup.nodes) {
          const position = positions[node.id]
          if (position) {
            if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) throw new Error('节点坐标无效')
            Object.assign(node, position)
          }
        }
      }),
      undo: () => travel('undo'),
      redo: () => travel('redo'),
      runExternal: (operation, options) => enqueue(async () => {
        const before = await api.exportJson()
        graphRequest += 1
        traceRequest += 1
        set({ trace: null })
        const accept = (backup: GraphBackup) => {
          // 未 apply、校验失败、幂等导入均不丢历史；真实外部写入才使旧历史失效。
          if (backup.revision !== before.revision) set({ past: [], future: [], historyRevision: null, fitVersion: get().fitVersion + 1 })
          publish(backup)
        }
        try {
          const result = await operation()
          const backup = await api.exportJson()
          const arranged = options?.applied?.(result) === false || options?.layout === false ? backup : arrangeUnpositioned(backup)
          const after = sameGraph(backup, arranged) ? backup : await restore(arranged, backup.revision)
          const view = options?.view?.(result)
          if (view && view !== get().view) set({ view, selection: null, canvasSelection: { nodes: [], edges: [] } })
          accept(after)
          return result
        } catch (error) {
          // 失败响应也可能来自已提交后的读取/布局失败，重新核对完整图修正画布与历史。
          try { accept(await api.exportJson()) }
          catch { await refresh().catch(() => undefined) }
          throw error
        }
      }),
    }
  })
}

export const useStore = createGraphStore()
