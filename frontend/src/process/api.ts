/** 独立流程文档 API；保存及删除均使用 revision 防止覆盖其他窗口的修改。 */
import type { AssetUsage, ProcessAsset, ProcessCreate, ProcessDiagram, ProcessSummary } from './types'

export class ProcessApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
    this.name = 'ProcessApiError'
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch { /* 保留非 JSON 响应的状态码。 */ }
    throw new ProcessApiError(detail, response.status)
  }
  return response.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

export const processApi = {
  list: () => request<ProcessSummary[]>('/api/diagrams'),
  get: (id: string) => request<ProcessDiagram>(`/api/diagrams/${encodeURIComponent(id)}`),
  create: (data: ProcessCreate) => request<ProcessDiagram>('/api/diagrams', json('POST', data)),
  update: (id: string, data: ProcessCreate & { expected_revision: string }) =>
    request<ProcessDiagram>(`/api/diagrams/${encodeURIComponent(id)}`, json('PUT', data)),
  remove: (id: string, revision: string) =>
    request<{ ok: true }>(`/api/diagrams/${encodeURIComponent(id)}?expected_revision=${encodeURIComponent(revision)}`, { method: 'DELETE' }),
  assets: () => request<ProcessAsset[]>('/api/assets'),
  assetUsage: (id: string) => request<AssetUsage[]>(`/api/assets/${encodeURIComponent(id)}/diagrams`),
}
