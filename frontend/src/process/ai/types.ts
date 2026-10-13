// POST /api/process/ai/draft 响应契约 v1（息流·架构，2026-10-13）
import type { ProcessCreate, ProcessEdge, ProcessNode } from '../types'

export type UncertaintyKind =
  | 'order' | 'missing_branch' | 'owner' | 'conflict' | 'missing_start' | 'missing_end' | 'unreachable' | 'other'

export interface Uncertainty {
  nodeId: string            // 对应 draft.nodes[].id
  kind: UncertaintyKind
  reason: string
  sourceQuote: string       // 原文片段，可能为空字符串
}

export interface AssetSuggestion {
  nodeId: string
  assetId: string           // 一定在 GET /api/process/assets 的可用资产中
  assetName: string
  mention: string           // 原文提到的说法
  confidence: number        // 0–1
  preselect: boolean        // 高置信且唯一 => true
}

export interface UnmatchedAsset { nodeId: string; mention: string }

export interface AiDraftResponse {
  draft: ProcessCreate      // assetIds 全为 []，坐标全为 0，需前端 layoutProcess()
  uncertainties: Uncertainty[]
  assetSuggestions: AssetSuggestion[]   // matchAssets=false 时为 []
  unmatchedAssets: UnmatchedAsset[]
  sourceText: string        // 解析后的原文，用于对照高亮
  stats: { model: string; latencyMs: number; llmCalls: 1 | 2; repaired: boolean }
}

export type AiDraftInputKind = 'description' | 'minutes' | 'sop'

// 请求：multipart/form-data
// text?: string | file?: File（二选一）, name?: string, matchAssets?: 'true'|'false'(默认 true),
// inputKind?: 'description'|'minutes'|'sop'

// 错误：{ detail: { code: AiDraftErrorCode, message: string } }
export type AiDraftErrorCode =
  | 'file_too_large'    // 413 文件超过 10 MiB
  | 'unsupported_type'  // 415 只支持 txt/md/docx/pdf
  | 'parse_failed'      // 422 文件损坏或无法读取
  | 'scanned_pdf'       // 422 PDF 无文本层（扫描件）
  | 'empty_text'        // 422 没有可用文字
  | 'text_too_long'     // 422 超过 20,000 字
  | 'not_a_process'     // 422 未识别到流程。message 为固定句，detail.reason 不展示
  | 'invalid_input'     // 422 同时传文字和文件，或参数不合法
  | 'llm_not_configured'// 503 未配置模型
  | 'llm_failed'        // 502 模型返回无法修复
  | 'llm_timeout'       // 504 超过 55 秒

// ---------------------------------------------------------------
// 多轮共创的类型先留在契约里。这一版不调用 POST /api/process/ai/chat。
export interface AiChatRequest {
  draft: ProcessCreate                 // 前端当前内存草稿（含用户手改）
  sourceText: string                   // 首版响应里的原文
  history: AiChatMessage[]             // 前端保存，最多保留最近 20 条
  message: string                      // 用户本轮输入或对卡片的回答
  answers?: { questionId: string; value: string }[]
  matchAssets?: boolean
}

export type AiChatMessage =
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; questions?: AiQuestion[] }

export interface AiQuestion {
  id: string
  nodeId?: string                      // 关联【待确认】节点，便于画布跳转
  text: string
  options?: string[]                   // 可点选；同时允许自由输入
}

// 一批原子操作，后端已校验，前端按顺序应用并存快照
export type DraftOp =
  | { op: 'add_node'; node: ProcessNode }            // x=y=0，前端只给新节点排版
  | { op: 'update_node'; id: string; patch: Partial<Omit<ProcessNode, 'id' | 'x' | 'y'>> }
  | { op: 'remove_node'; id: string }                // 连带删除相关连线
  | { op: 'add_edge'; edge: ProcessEdge }
  | { op: 'update_edge'; id: string; patch: Partial<Omit<ProcessEdge, 'id'>> }
  | { op: 'remove_edge'; id: string }

export interface AiChatResponse {
  reply: string                        // AI 本轮说明
  ops: DraftOp[]                       // 应用后结果保证通过 ProcessCreate 校验
  questions: AiQuestion[]              // 每轮 ≤ 3，优先【待确认】节点
  uncertainties: Uncertainty[]         // 应用 ops 后的完整最新清单
  assetSuggestions: AssetSuggestion[]  // 本轮新增建议
  stats: { model: string; latencyMs: number; llmCalls: number }
}
// 新增错误码：'draft_invalid'(422 传入草稿本身不合法) | 'ops_invalid'(502 修复后仍不合法)
