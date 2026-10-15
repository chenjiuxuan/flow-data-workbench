import { ProcessApiError } from '../api'
import type { ProcessDiagram } from '../types'

export const OVERWRITE_STALE_MESSAGE = '目标流程刚被其他页面修改，已刷新为最新版本。你的初稿还在，确认后可以再次覆盖'

/** 只有 409 才重新读取目标流程。其他错误返回 null，由调用方按原错误提示。 */
export async function recoverStaleOverwrite(
  error: unknown,
  targetId: string,
  read: (id: string) => Promise<ProcessDiagram>,
): Promise<ProcessDiagram | null> {
  if (!(error instanceof ProcessApiError) || error.status !== 409) return null
  return read(targetId)
}
