import { Alert, App as AntdApp, Button, Modal, Select } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useState } from 'react'
import { processApi } from '../api'
import { downloadProcessJson } from '../downloadJson'
import type { ProcessDiagram, ProcessSummary } from '../types'

interface Props {
  open: boolean
  diagrams: ProcessSummary[]
  onCancel: () => void
  /** 返回 false 表示用户取消了后续确认，弹窗保持打开。失败时抛出。 */
  onApply: (target: ProcessDiagram) => Promise<boolean>
}

function formatUpdatedAt(value: string) {
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value
}

export function OverwriteProcessModal({ open, diagrams, onCancel, onApply }: Props) {
  const { message } = AntdApp.useApp()
  const [pickedId, setPickedId] = useState<string | undefined>()
  const [target, setTarget] = useState<ProcessDiagram | null>(null)
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)

  useEffect(() => {
    if (open) return
    setPickedId(undefined)
    setTarget(null)
    setLoading(false)
    setApplying(false)
  }, [open])

  const loadTarget = async () => {
    if (!pickedId) { message.warning('请选择要覆盖的流程'); return }
    setLoading(true)
    try { setTarget(await processApi.get(pickedId)) }
    catch (error) { message.error(`读取流程失败：${(error as Error).message}`) }
    finally { setLoading(false) }
  }

  const apply = async () => {
    if (!target) return
    setApplying(true)
    try {
      const saved = await onApply(target)
      if (saved) onCancel()
    } catch { /* 调用方提示具体错误，弹窗留在确认页以便重试。 */ }
    finally { setApplying(false) }
  }

  return (
    <Modal
      title="覆盖现有流程"
      open={open}
      onCancel={() => !applying && onCancel()}
      destroyOnHidden
      footer={target ? [
        <Button key="back" disabled={applying} onClick={() => setTarget(null)}>返回选择</Button>,
        <Button key="ok" type="primary" danger loading={applying} onClick={() => void apply()}>确认覆盖</Button>,
      ] : [
        <Button key="cancel" onClick={onCancel}>取消</Button>,
        <Button key="next" type="primary" loading={loading} disabled={!pickedId} onClick={() => void loadTarget()}>下一步</Button>,
      ]}
    >
      {target ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <p style={{ margin: 0 }}>目标流程：{target.name}</p>
          <p style={{ margin: 0 }}>当前节点数：{target.nodeCount} · 连线 {target.edgeCount} 条</p>
          <p style={{ margin: 0 }}>最后修改：{formatUpdatedAt(target.updatedAt)}</p>
          <Alert type="warning" showIcon message="确认后会用当前初稿替换该流程的全部步骤和连线。建议先导出 JSON。" />
          <Button onClick={() => { downloadProcessJson(target); message.success('已导出目标流程 JSON') }}>导出 JSON</Button>
        </div>
      ) : diagrams.length === 0 ? (
        <Alert type="info" showIcon message="还没有可覆盖的流程图。可以改为保存为新流程。" />
      ) : (
        <Select
          showSearch
          optionFilterProp="label"
          placeholder="选择要覆盖的流程"
          style={{ width: '100%', marginTop: 12 }}
          value={pickedId}
          options={diagrams.map((item) => ({ value: item.id, label: `${item.name}（${item.nodeCount} 个节点）` }))}
          onChange={setPickedId}
        />
      )}
    </Modal>
  )
}
