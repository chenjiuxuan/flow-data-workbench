import type { AiDraftErrorCode } from './types'

/** 与当前 draft 接口文案一致。界面不出现 inputKind、matchAssets 等参数名。 */
export const AI_DRAFT_ERROR_MESSAGES: Record<AiDraftErrorCode, string> = {
  file_too_large: '文件超过 10 MiB，请压缩或拆分后再上传',
  unsupported_type: '只支持 txt、md、docx、pdf 文件',
  parse_failed: '文件已损坏或无法读取，请确认文件完整后重试',
  scanned_pdf: '这个 PDF 像是扫描件，读不到文字。请把里面的文字复制进来，或上传 Word/文本版',
  empty_text: '没有可用文字，请粘贴内容或上传包含文字的文件',
  text_too_long: '文字超过 20,000 字，请删减后再生成',
  not_a_process: '没有识别到流程。请描述谁在什么环节做什么、有哪些判断，或者直接贴会议纪要或 SOP 原文',
  invalid_input: '请只粘贴文字，或只上传一个文件',
  llm_not_configured: '未配置语言模型，无法生成流程初稿',
  llm_failed: '模型生成失败，请稍后重试',
  llm_timeout: '生成超时，请稍后重试',
}

const STATUS_FALLBACK: Partial<Record<number, AiDraftErrorCode>> = {
  413: 'file_too_large',
  415: 'unsupported_type',
  502: 'llm_failed',
  503: 'llm_not_configured',
  504: 'llm_timeout',
}

const PARAM_NAME = /inputKind|matchAssets|input_kind|match_assets/i

export function isAiDraftErrorCode(code: string): code is AiDraftErrorCode {
  return Object.prototype.hasOwnProperty.call(AI_DRAFT_ERROR_MESSAGES, code)
}

function mentionsParameterName(message: string) {
  return PARAM_NAME.test(message)
}

/** 旧接口把「同时传文字和文件 / 参数不合法」记成 empty_text，并把参数名写进 message。 */
function looksLikeInvalidInput(message: string) {
  return mentionsParameterName(message) || message.includes('请只提交文字或文件')
}

/**
 * not_a_process 展示接口里的固定 message。
 * 旧响应会把原因拼成「未识别到流程：……」，这里只留固定句。detail.reason 不进文案。
 */
export function notAProcessMessage(serverMessage: string) {
  const message = serverMessage.trim()
  if (!message) return AI_DRAFT_ERROR_MESSAGES.not_a_process
  if (/^未识别到流程(?:[：:].*)?$/.test(message)) return '未识别到流程'
  return message
}

function invalidInputMessage(serverMessage: string) {
  const message = serverMessage.trim()
  if (message && !looksLikeInvalidInput(message)) return message
  return AI_DRAFT_ERROR_MESSAGES.invalid_input
}

/** 已知错误码用固定文案。not_a_process 与合法的 invalid_input 保留接口 message。 */
export function messageForAiDraftCode(code: string | undefined, serverMessage: string, status: number) {
  if (code === 'not_a_process') return notAProcessMessage(serverMessage)
  if (code === 'invalid_input' || (code === 'empty_text' && looksLikeInvalidInput(serverMessage))) {
    return invalidInputMessage(serverMessage)
  }
  if ((code === 'llm_failed' || !code) && serverMessage.includes('未配置语言模型')) {
    return AI_DRAFT_ERROR_MESSAGES.llm_not_configured
  }
  if (code && isAiDraftErrorCode(code)) return AI_DRAFT_ERROR_MESSAGES[code]
  const fallback = STATUS_FALLBACK[status]
  if (fallback) return AI_DRAFT_ERROR_MESSAGES[fallback]
  if (serverMessage && !mentionsParameterName(serverMessage)) return serverMessage
  if (serverMessage) return AI_DRAFT_ERROR_MESSAGES.invalid_input
  return `生成失败（${status}）`
}

export const AI_DRAFT_MAX_FILE_BYTES = 10 * 1024 * 1024
export const AI_DRAFT_MAX_TEXT_LENGTH = 20_000
const ACCEPTED_EXTENSIONS = new Set(['txt', 'md', 'docx', 'pdf'])

export interface DraftInputCheck {
  mode: 'text' | 'file'
  text: string
  file: { name: string; size: number } | null
}

export function fileExtension(name: string) {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index + 1).toLowerCase() : ''
}

/** 提交前的本地检查，文案与对应服务端错误码一致。 */
export function validateDraftInput(input: DraftInputCheck): string | null {
  if (input.mode === 'file') {
    if (!input.file) return '请上传 txt、md、docx 或 pdf 文件。'
    if (!ACCEPTED_EXTENSIONS.has(fileExtension(input.file.name))) return AI_DRAFT_ERROR_MESSAGES.unsupported_type
    if (input.file.size > AI_DRAFT_MAX_FILE_BYTES) return AI_DRAFT_ERROR_MESSAGES.file_too_large
    return null
  }
  const text = input.text.trim()
  if (!text) return AI_DRAFT_ERROR_MESSAGES.empty_text
  if (text.length > AI_DRAFT_MAX_TEXT_LENGTH) return AI_DRAFT_ERROR_MESSAGES.text_too_long
  return null
}
