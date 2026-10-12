/** 后端 API 封装。 */
import type { DbDiscoverResult, ExcelImportResult, GraphData, MergeReport, SqlImportResult } from '../types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  if (!resp.ok) {
    let detail = `${resp.status}`
    try {
      const body = await resp.json()
      detail = body.detail ?? JSON.stringify(body)
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

const json = (data: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
})

export const api = {
  graph: (view: string) => request<GraphData>(`/api/graph?view=${view}`),

  trace: (nodeId: string, direction: string) =>
    request<GraphData>(`/api/trace/${encodeURIComponent(nodeId)}?direction=${direction}`),

  createNode: (data: { type: string; name: string; attrs?: Record<string, unknown>; x?: number; y?: number }) =>
    request<Record<string, never>>('/api/nodes', json(data)),

  patchNode: (id: string, data: Record<string, unknown>) =>
    request<Record<string, never>>(`/api/nodes/${encodeURIComponent(id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),

  deleteNode: (id: string) =>
    request<{ ok: boolean }>(`/api/nodes/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  createEdge: (data: { source_id: string; target_id: string; type: string; label?: string; attrs?: Record<string, unknown> }) =>
    request<Record<string, never>>('/api/edges', json(data)),

  patchEdge: (id: string, data: Record<string, unknown>) =>
    request<Record<string, never>>(`/api/edges/${encodeURIComponent(id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),

  deleteEdge: (id: string) =>
    request<{ ok: boolean }>(`/api/edges/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  saveLayout: (positions: Record<string, [number, number]>) =>
    request<{ saved: number }>('/api/layout', json({ positions })),

  importSql: (data: { sql_text: string; dialect: string; source_name: string; apply: boolean }) =>
    request<SqlImportResult>('/api/import/sql', json(data)),

  importExcel: async (file: File, apply: boolean) => {
    const form = new FormData()
    form.append('file', file)
    const resp = await fetch(`/api/import/excel?apply=${apply}`, { method: 'POST', body: form })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({ detail: `${resp.status}` }))
      throw new Error(body.detail ?? `${resp.status}`)
    }
    return resp.json() as Promise<ExcelImportResult>
  },

  discoverDb: (data: { db_type: string; host: string; port: number; user: string; password: string; database: string; apply: boolean }) =>
    request<DbDiscoverResult>('/api/discovery/db', json(data)),

  seedDemo: () => request<MergeReport>('/api/seed/demo', { method: 'POST' }),

  restoreJson: (data: unknown) => request<Record<string, number>>('/api/import/json', json(data)),
}
