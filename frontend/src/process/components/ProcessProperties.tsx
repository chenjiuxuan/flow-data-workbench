import { Alert, Button, Input, Select, Tag, Tooltip } from 'antd'
import { CloseOutlined, DeleteOutlined, LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { NODE_TYPE_META } from '../../components/meta'
import { PROCESS_NODE_META } from '../meta'
import { isUnsavedProcess, useProcessStore } from '../store'
import { nodeForm, normalizeNodeForm, reconcileDrafts, type NodeForm, type PropertyDraft } from '../formState'

interface Props {
  assetLoading: boolean
  assetError: string | null
  onRetryAssets: () => void
  onOpenAsset: (id: string) => void
  onDelete: (nodeIds: string[], edgeIds: string[]) => Promise<void>
  onSave: (operation: () => Promise<boolean>, label: string) => Promise<boolean>
}

export function ProcessProperties({ assetLoading, assetError, onRetryAssets, onOpenAsset, onDelete, onSave }: Props) {
  const current = useProcessStore((state) => state.current)
  const selection = useProcessStore((state) => state.selection)
  const assets = useProcessStore((state) => state.assets)
  const busy = useProcessStore((state) => state.busy || state.loading)
  const unsaved = isUnsavedProcess(current)
  // 切换选中项时保留当前文档中的未保存草稿；已保存值变化（包括撤销）后以服务端值为准。
  const [nodeDrafts, setNodeDrafts] = useState<Record<string, PropertyDraft<NodeForm>>>({})
  const [edgeDrafts, setEdgeDrafts] = useState<Record<string, PropertyDraft<string>>>({})
  useEffect(() => {
    setNodeDrafts((previous) => reconcileDrafts(previous, new Map((current?.nodes ?? []).map((item) => [item.id, JSON.stringify(nodeForm(item))]))))
    setEdgeDrafts((previous) => reconcileDrafts(previous, new Map((current?.edges ?? []).map((item) => [item.id, item.label]))))
  }, [current?.nodes, current?.edges])
  const node = selection?.kind === 'node' ? current?.nodes.find((item) => item.id === selection.id) : undefined
  const edge = selection?.kind === 'edge' ? current?.edges.find((item) => item.id === selection.id) : undefined
  if (!node && !edge) return null

  const baseline = node ? JSON.stringify(nodeForm(node)) : ''
  const draft = node ? nodeDrafts[node.id]?.baseline === baseline ? nodeDrafts[node.id].value : nodeForm(node) : null
  const edgeLabel = edge ? edgeDrafts[edge.id]?.baseline === edge.label ? edgeDrafts[edge.id].value : edge.label : ''
  const dirty = node ? JSON.stringify(draft) !== baseline : edgeLabel !== edge?.label
  const assetById = new Map(assets.map((asset) => [asset.id, asset]))
  const change = <K extends keyof NodeForm>(key: K, value: NodeForm[K]) => {
    if (node && draft) setNodeDrafts((previous) => ({ ...previous, [node.id]: { baseline, value: { ...draft, [key]: value } } }))
  }
  const save = async () => {
    if (busy || !dirty) return false
    if (node && draft) {
      if (!draft.name.trim()) return false
      const desired = normalizeNodeForm(draft)
      // 归一化后已与保存值一致也是成功，不能让“查看资产”把无变化当成保存失败。
      const saved = JSON.stringify(desired) === baseline || await onSave(() => useProcessStore.getState().edit((content) => {
        const target = content.nodes.find((item) => item.id === node.id)
        if (target) Object.assign(target, desired)
      }), unsaved ? '属性已记入初稿' : '属性已保存')
      if (saved) setNodeDrafts((previous) => {
        const next = { ...previous }
        delete next[node.id]
        return next
      })
      return saved
    }
    if (edge) {
      const saved = await onSave(() => useProcessStore.getState().edit((content) => {
        const target = content.edges.find((item) => item.id === edge.id)
        if (target) target.label = edgeLabel
      }), unsaved ? '连线条件已记入初稿' : '连线条件已保存')
      if (saved) setEdgeDrafts((previous) => {
        const next = { ...previous }
        delete next[edge.id]
        return next
      })
      return saved
    }
    return false
  }

  return (
    <aside className="process-properties" aria-label={node ? '节点属性' : '连线属性'}>
      <div className="process-properties__header">
        <div><h2>{node ? '节点属性' : '连线属性'}</h2><span>{node ? PROCESS_NODE_META[node.type].label : '有向连线'}</span></div>
        <Button type="text" size="small" icon={<CloseOutlined />} aria-label="收起属性面板" onClick={() => useProcessStore.getState().select(null)} />
      </div>
      <form
        className="process-properties__body"
        id="process-properties-form"
        onSubmit={(event) => { event.preventDefault(); void save() }}
      >
        {node && draft ? (
          <>
            <div className="process-field">
              <label htmlFor="process-node-name">节点名称 <span className="process-field__required">*</span></label>
              <Input id="process-node-name" maxLength={200} value={draft.name} disabled={busy} onChange={(event) => change('name', event.target.value)} status={!draft.name.trim() ? 'error' : undefined} />
              {!draft.name.trim() && <span className="process-field__error">请输入节点名称</span>}
            </div>
            <div className="process-field">
              <label htmlFor="process-node-owner">负责人 / 岗位</label>
              <Input id="process-node-owner" maxLength={300} value={draft.owner} disabled={busy} placeholder="例如：部门负责人" onChange={(event) => change('owner', event.target.value)} />
            </div>
            <div className="process-field">
              <label htmlFor="process-node-description">处理说明 / 规则</label>
              <Input.TextArea id="process-node-description" maxLength={4000} rows={3} value={draft.description} disabled={busy} placeholder="说明这个环节如何完成" onChange={(event) => change('description', event.target.value)} />
            </div>
            <div className="process-field">
              <label htmlFor="process-node-input">输入</label>
              <Input.TextArea id="process-node-input" maxLength={2000} rows={2} value={draft.input} disabled={busy} placeholder="需要哪些资料或数据" onChange={(event) => change('input', event.target.value)} />
            </div>
            <div className="process-field">
              <label htmlFor="process-node-output">产出</label>
              <Input.TextArea id="process-node-output" maxLength={2000} rows={2} value={draft.output} disabled={busy} placeholder="完成后产生什么" onChange={(event) => change('output', event.target.value)} />
            </div>
            <section className="process-assets" aria-label="关联资产">
              <div className="process-assets__heading"><label htmlFor="process-node-assets"><LinkOutlined /> 关联资产</label><Tooltip title="刷新共享资产列表"><Button type="text" size="small" icon={<ReloadOutlined />} aria-label="刷新共享资产列表" loading={assetLoading} onClick={onRetryAssets} /></Tooltip></div>
              <p>关联已有系统、数据表或接口；资产由所有流程共享。</p>
              {assetError ? (
                <Alert type="warning" showIcon message="资产列表加载失败" description={<><span>{assetError}</span><Button type="link" size="small" onClick={onRetryAssets}>重试</Button></>} />
              ) : (
                <Select
                  id="process-node-assets"
                  aria-label="选择关联资产"
                  mode="multiple"
                  showSearch
                  allowClear
                  optionFilterProp="label"
                  loading={assetLoading}
                  disabled={busy || assetLoading}
                  placeholder={assets.length ? '搜索并选择资产' : '尚无共享资产'}
                  value={draft.assetIds.filter((id) => assetById.has(id))}
                  options={assets.map((asset) => ({ value: asset.id, label: `${NODE_TYPE_META[asset.type].label} · ${asset.name}${asset.vanished ? '（源中已消失）' : ''}` }))}
                  onChange={(ids: string[]) => change('assetIds', [...draft.assetIds.filter((id) => !assetById.has(id)), ...ids])}
                  maxTagCount="responsive"
                  className="process-assets__select"
                />
              )}
              {!assetLoading && !assetError && assets.length === 0 && <p className="process-assets__empty">可先在“数据关系”中创建或导入资产，再回到这里关联。</p>}
              {draft.assetIds.length > 0 && (
                <ul className="process-assets__list">
                  {draft.assetIds.map((id) => {
                    const asset = assetById.get(id)
                    return <li key={id}>
                      <div className="process-assets__identity">
                        <span title={asset?.name ?? id}>{asset?.name ?? (assetLoading ? '正在加载资产信息' : assetError ? '资产信息暂不可用' : '已失效的资产关联')}</span>
                        <small>{asset ? `${NODE_TYPE_META[asset.type].label}${asset.vanished ? ' · 源中已消失' : ''}` : id}</small>
                      </div>
                      {asset && <Button type="link" size="small" disabled={busy || dirty && !draft.name.trim()} onClick={async () => {
                        if (!dirty || await save()) onOpenAsset(id)
                      }}>查看</Button>}
                      <Button type="text" size="small" disabled={busy} icon={<CloseOutlined />} aria-label={`移除关联：${asset?.name ?? id}`} onClick={() => change('assetIds', draft.assetIds.filter((item) => item !== id))} />
                    </li>
                  })}
                </ul>
              )}
            </section>
          </>
        ) : edge ? (
          <>
            <div className="process-edge-context">
              <div><span>起点</span><strong>{current?.nodes.find((item) => item.id === edge.source)?.name}</strong></div>
              <div><span>终点</span><strong>{current?.nodes.find((item) => item.id === edge.target)?.name}</strong></div>
            </div>
            <div className="process-field">
              <label htmlFor="process-edge-label">条件 / 连线说明</label>
              <Input.TextArea id="process-edge-label" maxLength={300} rows={3} disabled={busy} value={edgeLabel} placeholder="例如：审核通过、资料不完整" onChange={(event) => setEdgeDrafts((previous) => ({ ...previous, [edge.id]: { baseline: edge.label, value: event.target.value } }))} />
              <span className="process-field__hint">判断节点的分支可填写具体条件；普通连线可留空。</span>
            </div>
          </>
        ) : null}
      </form>
      <div className="process-properties__footer">
        <div className="process-properties__state">{dirty ? <Tag color="processing">有未保存修改</Tag> : <span>{unsaved ? '已记入初稿' : '已保存到当前流程'}</span>}</div>
        <div className="process-properties__actions">
          <Button danger icon={<DeleteOutlined />} disabled={busy} onClick={() => void onDelete(node ? [node.id] : [], edge ? [edge.id] : [])}>{node ? '删除节点' : '删除连线'}</Button>
          <Button type="primary" htmlType="submit" form="process-properties-form" disabled={busy || !dirty || !!draft && !draft.name.trim()} loading={busy}>{unsaved ? '记入初稿' : '保存修改'}</Button>
        </div>
      </div>
    </aside>
  )
}
