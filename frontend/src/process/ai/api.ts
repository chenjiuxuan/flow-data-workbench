/** 只读生成接口。取消走 AbortSignal；保存仍使用现有流程图 POST / PUT。 */
import { AI_DRAFT_ERROR_MESSAGES, messageForAiDraftCode } from './messages'
import type { AiDraftInputKind, AiDraftResponse } from './types'

export class AiDraftError extends Error {
  readonly status: number
  readonly code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'AiDraftError'
    this.status = status
    this.code = code
  }
}

export class AiDraftAbortError extends Error {
  constructor() {
    super('已取消生成')
    this.name = 'AiDraftAbortError'
  }
}

export interface AiDraftRequest {
  text?: string
  file?: File
  name?: string
  /** 默认 true。表单里写成 'true' | 'false'。 */
  matchAssets?: boolean
  inputKind?: AiDraftInputKind
}

function aborted(error: unknown, signal?: AbortSignal) {
  if (signal?.aborted) return true
  return error instanceof Error && error.name === 'AbortError'
}

export async function requestAiDraft(input: AiDraftRequest, signal?: AbortSignal): Promise<AiDraftResponse> {
  const text = input.text?.trim() ?? ''
  const file = input.file
  if (Boolean(text) === Boolean(file)) {
    throw new AiDraftError(
      text ? AI_DRAFT_ERROR_MESSAGES.invalid_input : '请粘贴文字或上传一个文件',
      422,
      'invalid_input',
    )
  }
  const body = new FormData()
  if (text) body.append('text', text)
  if (file) body.append('file', file)
  const name = input.name?.trim()
  if (name) body.append('name', name)
  body.append('matchAssets', input.matchAssets === false ? 'false' : 'true')
  if (input.inputKind) body.append('inputKind', input.inputKind)

  let response: Response
  try {
    response = await fetch('/api/process/ai/draft', { method: 'POST', body, signal })
  } catch (error) {
    if (aborted(error, signal)) throw new AiDraftAbortError()
    throw new AiDraftError('网络异常，生成请求没有完成。', 0)
  }
  if (signal?.aborted) throw new AiDraftAbortError()
  if (!response.ok) {
    let code: string | undefined
    let serverMessage = ''
    try {
      const payload = await response.json() as { detail?: unknown }
      const detail = payload.detail
      if (detail && typeof detail === 'object') {
        const record = detail as { code?: unknown; message?: unknown }
        code = typeof record.code === 'string' ? record.code : undefined
        serverMessage = typeof record.message === 'string' ? record.message : ''
        // detail.reason 只给排查用，不拼进展示文案。
      } else if (typeof detail === 'string') serverMessage = detail
    } catch { /* 非 JSON 错误仍按状态码给文案。 */ }
    throw new AiDraftError(messageForAiDraftCode(code, serverMessage, response.status), response.status, code)
  }
  return response.json() as Promise<AiDraftResponse>
}
