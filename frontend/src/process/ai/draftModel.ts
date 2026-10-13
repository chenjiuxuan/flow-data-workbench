import { layoutProcess } from '../layout'
import type { ProcessCreate } from '../types'
import type { AssetSuggestion, Uncertainty } from './types'

/** 模型写在名称上的标记。前端只统计和展示，保存时不补回。 */
export const PENDING_REVIEW_PREFIX = '【待确认】'
const MAX_NODE_ASSETS = 100

export function countPendingReview(nodes: { name: string }[]) {
  return nodes.filter((node) => node.name.startsWith(PENDING_REVIEW_PREFIX)).length
}

export function suggestionKey(suggestion: { nodeId: string; assetId: string }) {
  return `${suggestion.nodeId}\0${suggestion.assetId}`
}

export function initialSelectedKeys(suggestions: AssetSuggestion[]) {
  return new Set(suggestions.filter((item) => item.preselect).map(suggestionKey))
}

/**
 * 把勾选的资产建议并进对应节点的 assetIds。
 * 不改名称、说明和坐标，因此不会把用户删掉的【待确认】再写回去。
 */
export function applyAssetSuggestions(
  draft: ProcessCreate,
  suggestions: AssetSuggestion[],
  selected: ReadonlySet<string>,
): ProcessCreate {
  const grouped = new Map<string, string[]>()
  const seen = new Map<string, Set<string>>()
  for (const suggestion of suggestions) {
    if (!selected.has(suggestionKey(suggestion))) continue
    if (!suggestion.nodeId || !suggestion.assetId) continue
    const bag = seen.get(suggestion.nodeId) ?? new Set<string>()
    if (bag.has(suggestion.assetId)) continue
    bag.add(suggestion.assetId)
    seen.set(suggestion.nodeId, bag)
    const list = grouped.get(suggestion.nodeId) ?? []
    list.push(suggestion.assetId)
    grouped.set(suggestion.nodeId, list)
  }
  let changed = false
  const nodes = draft.nodes.map((node) => {
    const extras = grouped.get(node.id)
    if (!extras?.length) return node
    const assetIds = [...node.assetIds]
    for (const assetId of extras) {
      if (assetIds.includes(assetId)) continue
      if (assetIds.length >= MAX_NODE_ASSETS) break
      assetIds.push(assetId)
    }
    if (assetIds.length === node.assetIds.length) return node
    changed = true
    return { ...node, assetIds }
  })
  return changed ? { ...draft, nodes } : draft
}

/** 生成结果坐标全是 0，预览和进入编辑前都走现有自动布局。 */
export function withLayout(draft: ProcessCreate): ProcessCreate {
  const positions = layoutProcess(draft.nodes, draft.edges)
  return {
    ...draft,
    nodes: draft.nodes.map((node) => {
      const position = positions.get(node.id)
      if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return node
      return { ...node, x: position.x, y: position.y }
    }),
  }
}

/** 预览里给「从开始接上、原文接不上」的边用的临时标签，不写回初稿。 */
export const UNREACHABLE_LINK_LABEL = '衔接不明'

export const UNREACHABLE_EXPLANATION = '这条步骤在原文里接不上主流程。从「开始」直接连过去，是为了让它留在图里，并不是并列分支。'

export function unreachableNodeIds(uncertainties: Pick<Uncertainty, 'nodeId' | 'kind'>[]) {
  return new Set(uncertainties.filter((item) => item.kind === 'unreachable' && item.nodeId).map((item) => item.nodeId))
}

/** 编译器会从开始节点补一条边，让接不上的步骤仍留在图上。 */
export function startBridgeTargetIds(
  nodes: { id: string; type: string }[],
  edges: { source: string; target: string }[],
  unreachable: ReadonlySet<string>,
) {
  const starts = new Set(nodes.filter((node) => node.type === 'start').map((node) => node.id))
  const targets = new Set<string>()
  for (const edge of edges) {
    if (starts.has(edge.source) && unreachable.has(edge.target)) targets.add(edge.target)
  }
  return targets
}

export function previewEdgeLabel(label: string, bridged: boolean) {
  if (bridged && !label.trim()) return UNREACHABLE_LINK_LABEL
  return label
}

/** 衔接不明的步骤排在不确定项最前，避免被看成普通备注。 */
export function orderUncertainties<T extends { kind: string }>(items: readonly T[]) {
  return [...items].sort((left, right) => Number(left.kind !== 'unreachable') - Number(right.kind !== 'unreachable'))
}

export interface SourceSegment { text: string; hit: boolean; active: boolean }

/** 把原文按 sourceQuote 切开，供对照页高亮。空引用和重叠片段跳过。 */
export function highlightSource(source: string, quotes: string[], activeQuote: string): SourceSegment[] {
  const ranges: { start: number; end: number; active: boolean }[] = []
  const unique = [...new Set(quotes.map((quote) => quote.trim()).filter(Boolean))]
  unique.sort((a, b) => b.length - a.length)
  const active = activeQuote.trim()
  for (const quote of unique) {
    let from = 0
    while (from < source.length) {
      const index = source.indexOf(quote, from)
      if (index < 0) break
      const end = index + quote.length
      if (!ranges.some((range) => index < range.end && end > range.start)) {
        ranges.push({ start: index, end, active: quote === active })
      }
      from = end
    }
  }
  ranges.sort((a, b) => a.start - b.start)
  const segments: SourceSegment[] = []
  let cursor = 0
  for (const range of ranges) {
    if (range.start > cursor) segments.push({ text: source.slice(cursor, range.start), hit: false, active: false })
    segments.push({ text: source.slice(range.start, range.end), hit: true, active: range.active })
    cursor = range.end
  }
  if (cursor < source.length || segments.length === 0) segments.push({ text: source.slice(cursor), hit: false, active: false })
  return segments
}
