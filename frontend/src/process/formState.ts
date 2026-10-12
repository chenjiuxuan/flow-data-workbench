import type { ProcessNode } from './types'

export type NodeForm = Pick<ProcessNode, 'name' | 'owner' | 'description' | 'input' | 'output' | 'assetIds'>
export interface PropertyDraft<T> { baseline: string; value: T }

export const nodeForm = (node: ProcessNode): NodeForm => ({
  name: node.name, owner: node.owner, description: node.description,
  input: node.input, output: node.output, assetIds: node.assetIds,
})

export const normalizeNodeForm = (form: NodeForm): NodeForm => ({ ...form, name: form.name.trim() })

/** 服务端字段一旦变更就丢弃对应旧草稿，避免撤销回旧 baseline 时复活已提交的值。 */
export function reconcileDrafts<T>(drafts: Record<string, PropertyDraft<T>>, baselines: Map<string, string>) {
  const entries = Object.entries(drafts)
  const retained = entries.filter(([id, draft]) => baselines.get(id) === draft.baseline)
  return retained.length === entries.length ? drafts : Object.fromEntries(retained)
}
