/** 文档写入串行执行；历史只保存内容，避免撤销时回滚流程名称与说明。 */
import { create } from 'zustand'
import { processApi } from './api'
import type { ProcessAsset, ProcessContent, ProcessCreate, ProcessDiagram, ProcessSummary } from './types'

export interface ProcessSelection { kind: 'node' | 'edge'; id: string }
interface History { revision: string; past: ProcessContent[]; future: ProcessContent[] }
export interface ProcessStore {
  diagrams: ProcessSummary[]
  current: ProcessDiagram | null
  assets: ProcessAsset[]
  loading: boolean
  busy: boolean
  selection: ProcessSelection | null
  past: ProcessContent[]
  future: ProcessContent[]
  fitVersion: number
  error: string | null
  loadList: () => Promise<void>
  open: (id: string) => Promise<void>
  close: () => void
  create: (data: ProcessCreate) => Promise<ProcessDiagram>
  rename: (id: string, name: string, description: string, expectedRevision?: string) => Promise<void>
  remove: (id: string, expectedRevision?: string) => Promise<void>
  loadAssets: () => Promise<void>
  edit: (mutator: (draft: ProcessContent) => void) => Promise<boolean>
  undo: () => Promise<boolean>
  redo: () => Promise<boolean>
  select: (selection: ProcessSelection | null) => void
}

const content = (diagram: ProcessContent): ProcessContent => structuredClone({ nodes: diagram.nodes, edges: diagram.edges })
const summary = ({ nodes: _nodes, edges: _edges, ...data }: ProcessDiagram): ProcessSummary => data
const sameContent = (a: ProcessContent, b: ProcessContent) => JSON.stringify(a) === JSON.stringify(b)
const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error)

export function createProcessStore() {
  let openRequest = 0
  let listRequest = 0
  let assetRequest = 0
  let listLoading = false
  let documentLoading = false
  let openingId: string | null = null
  let pending = 0
  let queue: Promise<unknown> = Promise.resolve()
  const histories = new Map<string, History>()

  return create<ProcessStore>((set, get) => {
    const updateLoading = () => set({ loading: listLoading || documentLoading })
    const putSummary = (diagram: ProcessDiagram) => set({
      diagrams: [summary(diagram), ...get().diagrams.filter((item) => item.id !== diagram.id)],
    })
    const invalidateList = () => {
      listRequest += 1
      listLoading = false
      updateLoading()
    }
    const cancelOpen = () => {
      openRequest += 1
      openingId = null
      documentLoading = false
      updateLoading()
    }
    const keepSelection = (diagram: ProcessDiagram) => {
      const selection = get().selection
      return selection && (selection.kind === 'node' ? diagram.nodes : diagram.edges).some((item) => item.id === selection.id)
        ? selection : null
    }
    const publish = (diagram: ProcessDiagram, past: ProcessContent[], future: ProcessContent[]) => {
      histories.set(diagram.id, { revision: diagram.revision, past, future })
      putSummary(diagram)
      set({ current: diagram, past, future, selection: keepSelection(diagram), error: null })
    }
    const enqueue = <T,>(operation: () => Promise<T>, replacesCurrent = false, documentId: string | null = null): Promise<T> => {
      pending += 1
      // 内容写入与新建会替换当前文档；后台文档的修改不取消正在打开的其他文档。
      if (replacesCurrent) cancelOpen()
      invalidateList()
      // 无变化操作没有修复先前的保存冲突；只在权威内容成功发布时清除错误。
      set({ busy: true })
      const result = queue.then(operation).catch((error: unknown) => {
        // 后台文档或创建框的失败由调用方提示，不把当前文档误标为保存失败。
        if (documentId && (get().current?.id === documentId || openingId === documentId)) {
          set({ error: errorMessage(error) })
        }
        throw error
      })
      queue = result.catch(() => undefined)
      return result.finally(() => {
        pending -= 1
        set({ busy: pending > 0 })
      })
    }
    const saveContent = (diagram: ProcessDiagram, desired: ProcessContent) => processApi.update(diagram.id, {
      name: diagram.name, description: diagram.description, ...desired, expected_revision: diagram.revision,
    })
    const travel = (direction: 'undo' | 'redo') => {
      const id = get().current?.id
      if (!id || get().loading) return Promise.resolve(false)
      return enqueue(async () => {
        const state = get()
        if (state.current?.id !== id) return false
        const snapshots = direction === 'undo' ? state.past : state.future
        const desired = snapshots.at(-1)
        if (!desired) return false
        const before = content(state.current)
        const saved = await saveContent(state.current, content(desired))
        invalidateList()
        publish(saved,
          direction === 'undo' ? state.past.slice(0, -1) : [...state.past, before].slice(-100),
          direction === 'undo' ? [...state.future, before].slice(-100) : state.future.slice(0, -1),
        )
        return true
      }, true, id)
    }

    return {
      diagrams: [], current: null, assets: [], loading: false, busy: false,
      selection: null, past: [], future: [], fitVersion: 0, error: null,
      loadList: async () => {
        const request = ++listRequest
        listLoading = true
        updateLoading()
        try {
          const diagrams = await processApi.list()
          if (request === listRequest) set({ diagrams })
        } catch (error) {
          if (request === listRequest) throw error
        } finally {
          if (request === listRequest) { listLoading = false; updateLoading() }
        }
      },
      open: async (id) => {
        if (get().busy) return
        const request = ++openRequest
        openingId = id
        documentLoading = true
        set({ error: null })
        updateLoading()
        try {
          const diagram = await processApi.get(id)
          if (request !== openRequest) return
          const savedHistory = histories.get(id)
          const history = savedHistory?.revision === diagram.revision ? savedHistory : { past: [], future: [] }
          // 详情已确认更新的名称与版本，旧目录响应不能再把它回滚。
          invalidateList()
          set({ selection: null, fitVersion: get().fitVersion + 1 })
          publish(diagram, history.past, history.future)
        } catch (error) {
          if (request === openRequest) { set({ error: errorMessage(error) }); throw error }
        } finally {
          if (request === openRequest) { openingId = null; documentLoading = false; updateLoading() }
        }
      },
      close: () => {
        if (get().busy) return
        cancelOpen()
        set({ current: null, selection: null, past: [], future: [], error: null })
        updateLoading()
      },
      create: (data) => enqueue(async () => {
        const diagram = await processApi.create(data)
        invalidateList()
        set({ selection: null, fitVersion: get().fitVersion + 1 })
        publish(diagram, [], [])
        return diagram
      }, true),
      rename: (id, name, description, expectedRevision) => enqueue(async () => {
        const state = get()
        const diagram = state.current?.id === id ? state.current : await processApi.get(id)
        // 表单打开时看到的版本优先，后台刷新不能替用户批准较新的内容。
        const revision = expectedRevision ?? (state.current?.id === id ? diagram.revision
          : state.diagrams.find((item) => item.id === id)?.revision ?? diagram.revision)
        const saved = await processApi.update(id, { ...content(diagram), name, description, expected_revision: revision })
        invalidateList()
        const openingThis = openingId === id
        if (openingThis) {
          // 重命名的保存响应比此前的 GET 更新，直接完成同一文档的打开。
          cancelOpen()
          set({ selection: null, fitVersion: get().fitVersion + 1 })
        }
        const history = histories.get(id)
        if (get().current?.id === id || openingThis) {
          const matching = history?.revision === diagram.revision ? history : { past: [], future: [] }
          publish(saved, matching.past, matching.future)
        }
        else {
          putSummary(saved)
          if (history?.revision === diagram.revision) histories.set(id, { ...history, revision: saved.revision })
        }
      }, false, id),
      remove: (id, expectedRevision) => enqueue(async () => {
        const state = get()
        const revision = expectedRevision ?? (state.current?.id === id ? state.current.revision
          : state.diagrams.find((item) => item.id === id)?.revision ?? (await processApi.get(id)).revision)
        await processApi.remove(id, revision)
        if (openingId === id) cancelOpen()
        invalidateList()
        histories.delete(id)
        set({ diagrams: get().diagrams.filter((item) => item.id !== id) })
        if (get().current?.id === id) set({ current: null, selection: null, past: [], future: [] })
      }, false, id),
      loadAssets: async () => {
        const request = ++assetRequest
        try {
          const assets = await processApi.assets()
          if (request === assetRequest) set({ assets })
        } catch (error) {
          // 资产列表有独立的重试提示，不覆盖当前文档的保存/冲突状态。
          if (request === assetRequest) throw error
        }
      },
      edit: (mutator) => {
        const id = get().current?.id
        if (!id || get().loading) return Promise.resolve(false)
        return enqueue(async () => {
          const state = get()
          if (state.current?.id !== id) return false
          const before = content(state.current)
          const desired = content(before)
          mutator(desired)
          if (sameContent(before, desired)) return false
          const saved = await saveContent(state.current, desired)
          invalidateList()
          publish(saved, [...state.past, before].slice(-100), [])
          return true
        }, true, id)
      },
      undo: () => travel('undo'),
      redo: () => travel('redo'),
      select: (selection) => set({ selection }),
    }
  })
}

export const useProcessStore = createProcessStore()
