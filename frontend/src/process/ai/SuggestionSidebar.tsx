import { Checkbox, Tabs } from 'antd'
import { useMemo } from 'react'
import type { ProcessCreate } from '../types'
import { highlightSource, orderUncertainties, startBridgeTargetIds, suggestionKey, unreachableNodeIds, UNREACHABLE_EXPLANATION } from './draftModel'
import type { AssetSuggestion, Uncertainty, UncertaintyKind, UnmatchedAsset } from './types'

const KIND_LABEL: Record<UncertaintyKind, string> = {
  order: '顺序不确定',
  missing_branch: '缺少分支',
  owner: '负责人不确定',
  conflict: '原文冲突',
  missing_start: '缺少开始',
  missing_end: '缺少结束',
  unreachable: '衔接不明',
  other: '其他不确定',
}

interface Props {
  draft: ProcessCreate
  uncertainties: Uncertainty[]
  assetSuggestions: AssetSuggestion[]
  unmatchedAssets: UnmatchedAsset[]
  sourceText: string
  selectedKeys: ReadonlySet<string>
  focusNodeId: string | null
  onToggle: (key: string, checked: boolean) => void
  onFocusNode: (nodeId: string | null) => void
}

export function SuggestionSidebar({
  draft, uncertainties, assetSuggestions, unmatchedAssets, sourceText,
  selectedKeys, focusNodeId, onToggle, onFocusNode,
}: Props) {
  const names = useMemo(() => new Map(draft.nodes.map((node) => [node.id, node.name])), [draft.nodes])
  const orderedUncertainties = useMemo(() => orderUncertainties(uncertainties), [uncertainties])
  const bridged = useMemo(
    () => startBridgeTargetIds(draft.nodes, draft.edges, unreachableNodeIds(uncertainties)),
    [draft.edges, draft.nodes, uncertainties],
  )
  const activeQuote = uncertainties.find((item) => item.nodeId === focusNodeId)?.sourceQuote ?? ''
  const segments = useMemo(
    () => highlightSource(sourceText, uncertainties.map((item) => item.sourceQuote), activeQuote),
    [activeQuote, sourceText, uncertainties],
  )
  const groups = useMemo(() => {
    const ordered = draft.nodes.map((node) => node.id)
    const extra = assetSuggestions.map((item) => item.nodeId).filter((id) => !ordered.includes(id))
    return [...ordered, ...new Set(extra)].map((nodeId) => ({
      nodeId,
      items: assetSuggestions.filter((item) => item.nodeId === nodeId),
    })).filter((group) => group.items.length > 0)
  }, [assetSuggestions, draft.nodes])

  return (
    <aside className="ai-draft__side" aria-label="初稿说明">
      <Tabs
        defaultActiveKey="uncertainty"
        items={[
          {
            key: 'uncertainty',
            label: `不确定项 ${uncertainties.length}`,
            children: (
              <div className="ai-draft-tab">
                <p className="ai-draft-tab__lead">带【待确认】的步骤会原样进入编辑。点一条可在图上定位，名称可以稍后改掉。</p>
                {uncertainties.length === 0 && <p className="ai-draft-tab__lead">这次没有标出不确定项。</p>}
                {orderedUncertainties.map((item, index) => (
                  <button
                    key={`${item.nodeId}-${index}`}
                    type="button"
                    className={`ai-draft-item${item.kind === 'unreachable' ? ' ai-draft-item--unreachable' : ''}${item.nodeId === focusNodeId ? ' is-active' : ''}`}
                    onClick={() => onFocusNode(item.nodeId)}
                  >
                    <span className={`ai-draft-kind${item.kind === 'unreachable' ? ' ai-draft-kind--unreachable' : ''}`}>{KIND_LABEL[item.kind] ?? item.kind}</span>
                    <strong>{names.get(item.nodeId) || '未找到对应步骤'}</strong>
                    <p>{item.reason}</p>
                    {item.kind === 'unreachable' && (
                      <p className="ai-draft-unreachable">
                        {bridged.has(item.nodeId)
                          ? UNREACHABLE_EXPLANATION
                          : '这条步骤在原文里接不上主流程，并不是并列分支。'}
                      </p>
                    )}
                    {item.sourceQuote && <small>原文：{item.sourceQuote}</small>}
                  </button>
                ))}
              </div>
            ),
          },
          {
            key: 'assets',
            label: `资产建议 ${assetSuggestions.length}`,
            children: (
              <div className="ai-draft-tab">
                <p className="ai-draft-tab__lead">默认勾选的是高置信且唯一的匹配。取消勾选后，进入编辑时不会写入该步骤。</p>
                {assetSuggestions.length === 0 && <p className="ai-draft-tab__lead">没有可采纳的资产建议。</p>}
                {groups.map((group) => (
                  <section key={group.nodeId}>
                    <p className="ai-draft-node-label">
                      <button type="button" onClick={() => onFocusNode(group.nodeId)}>{names.get(group.nodeId) || group.nodeId}</button>
                    </p>
                    {group.items.map((item) => {
                      const key = suggestionKey(item)
                      return (
                        <label key={key} className="ai-draft-asset">
                          <Checkbox checked={selectedKeys.has(key)} onChange={(event) => onToggle(key, event.target.checked)} />
                          <span>
                            <strong>{item.assetName}</strong>
                            <small>原文说法「{item.mention}」· 置信度 {Math.round(item.confidence * 100)}%</small>
                          </span>
                        </label>
                      )
                    })}
                  </section>
                ))}
                {unmatchedAssets.length > 0 && (
                  <section>
                    <p className="ai-draft-node-label">未匹配资产</p>
                    <p className="ai-draft-tab__lead">这些名称没有对应的可用资产。可先到数据关系总图创建，再在编辑器里关联。</p>
                    <ul className="ai-draft-unmatched">
                      {unmatchedAssets.map((item, index) => (
                        <li key={`${item.nodeId}-${item.mention}-${index}`}>{item.mention}{names.get(item.nodeId) ? ` · ${names.get(item.nodeId)}` : ''}</li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
            ),
          },
          {
            key: 'source',
            label: '原文对照',
            children: (
              <div className="ai-draft-tab">
                <p className="ai-draft-tab__lead">高亮来自不确定项里的原文片段。当前定位的片段颜色更深。</p>
                <p className="ai-draft-source">
                  {segments.map((segment, index) => segment.hit
                    ? <mark key={index} className={segment.active ? 'is-active' : undefined}>{segment.text}</mark>
                    : <span key={index}>{segment.text}</span>)}
                </p>
              </div>
            ),
          },
          {
            key: 'chat',
            label: '对话',
            children: (
              <div className="ai-draft-tab">
                <p className="ai-draft-chat">多轮共创还没开放。这一版只根据当前文字或文件生成一次初稿；要改步骤，请进入编辑后直接调整。</p>
              </div>
            ),
          },
        ]}
      />
    </aside>
  )
}
