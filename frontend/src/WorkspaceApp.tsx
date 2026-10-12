import { ApartmentOutlined, DatabaseOutlined, DeleteOutlined, EditOutlined, FolderOpenOutlined, ImportOutlined, MoreOutlined, PlusOutlined } from '@ant-design/icons'
import { ReactFlowProvider } from '@xyflow/react'
import { Alert, App as AntdApp, Button, Drawer, Dropdown, Form, Input, Modal, Radio, Spin, Upload } from 'antd'
import { useCallback, useEffect, useRef, useState } from 'react'
import DataWorkspace from './App'
import { ProcessEditor } from './process/ProcessEditor'
import { useProcessStore } from './process/store'
import { approvalProcess, emptyProcess } from './process/templates'
import type { ProcessSummary } from './process/types'
import { useStore } from './store'
import './workspace.css'

interface DocumentForm { name: string; description: string; template: 'blank' | 'approval' }

export default function WorkspaceApp() {
  const { message, modal } = AntdApp.useApp()
  const diagrams = useProcessStore((s) => s.diagrams)
  const current = useProcessStore((s) => s.current)
  const busy = useProcessStore((s) => s.busy)
  const loading = useProcessStore((s) => s.loading)
  const error = useProcessStore((s) => s.error)
  const dataBusy = useStore((s) => s.busy)
  const [active, setActive] = useState('data')
  const [directoryOpen, setDirectoryOpen] = useState(false)
  const [directoryError, setDirectoryError] = useState<string | null>(null)
  const [editing, setEditing] = useState<ProcessSummary | 'new' | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const mutationLock = useRef(false)
  const navigation = useRef(0)
  const [form] = Form.useForm<DocumentForm>()
  const locked = busy || dataBusy || submitting

  const refreshDirectory = useCallback(async () => {
    setDirectoryError(null)
    try { await useProcessStore.getState().loadList() }
    catch (e) { setDirectoryError((e as Error).message) }
  }, [])
  useEffect(() => { void refreshDirectory() }, [refreshDirectory])

  const openProcess = useCallback(async (id: string) => {
    if (mutationLock.current || useProcessStore.getState().busy || useStore.getState().busy) return
    navigation.current += 1
    useStore.getState().select(null)
    setActive(id)
    setDirectoryOpen(false)
    try { await useProcessStore.getState().open(id) }
    catch (e) { message.error(`打开失败：${(e as Error).message}`) }
  }, [message])

  const openData = () => {
    if (locked) return
    navigation.current += 1
    useProcessStore.getState().close()
    setActive('data')
    setDirectoryOpen(false)
  }

  const openAsset = useCallback(async (id: string) => {
    if (useProcessStore.getState().busy) return
    const request = ++navigation.current
    const asset = useProcessStore.getState().assets.find((item) => item.id === id)
    if (!asset) { message.warning('该资产已删除或暂不可用，请刷新资产列表'); return }
    const view = asset.type === 'table' ? 'field' : asset.type === 'process_step' ? 'business' : 'system'
    try {
      await useStore.getState().setView(view)
      if (request !== navigation.current) return
      if (!useStore.getState().nodes.some((node) => node.id === id)) {
        message.warning('该资产已删除或暂不可用，请刷新资产列表')
        return
      }
      useStore.getState().setCanvasSelection([id], [])
      useStore.getState().select({ kind: 'node', id })
      useProcessStore.getState().close()
      setActive('data')
    } catch (e) { message.error(`打开资产失败：${(e as Error).message}`) }
  }, [message])

  const editDocument = (document: ProcessSummary | 'new') => {
    setEditing(document)
    form.setFieldsValue(document === 'new'
      ? { name: '', description: '', template: 'blank' }
      : { name: document.name, description: document.description, template: 'blank' })
  }

  const saveDocument = async () => {
    const values = await form.validateFields().catch(() => null)
    if (!values || !editing || mutationLock.current || useProcessStore.getState().busy || useStore.getState().busy) return
    mutationLock.current = true
    setSubmitting(true)
    try {
      if (editing === 'new') {
        const build = values.template === 'approval' ? approvalProcess : emptyProcess
        const created = await useProcessStore.getState().create(build(values.name.trim(), values.description?.trim() || ''))
        navigation.current += 1
        useStore.getState().select(null)
        setActive(created.id)
        setDirectoryOpen(false)
        message.success('流程图已创建')
      } else {
        await useProcessStore.getState().rename(editing.id, values.name.trim(), values.description?.trim() || '', editing.revision)
        message.success('流程信息已保存')
      }
      setEditing(null)
    } catch (e) { message.error(`保存失败：${(e as Error).message}`) }
    finally { mutationLock.current = false; setSubmitting(false) }
  }

  const removeDocument = (document: ProcessSummary) => modal.confirm({
    title: `删除「${document.name}」？`,
    content: '此操作会删除这张流程图及其中的步骤，关联的系统和数据资产会保留。可先导出流程文件留存。',
    okText: '删除流程图', okType: 'danger',
    onOk: async () => {
      try {
        await useProcessStore.getState().remove(document.id, document.revision)
        if (active === document.id) setActive('data')
        message.success('流程图已删除')
      } catch (e) { message.error(`删除失败：${(e as Error).message}`); throw e }
    },
  })

  const importFlow = async (file: File) => {
    if (mutationLock.current || useProcessStore.getState().busy || useStore.getState().busy) throw new Error('正在保存，请稍候')
    if (file.size > 10 * 1024 * 1024) throw new Error('流程文件最大支持 10 MiB')
    mutationLock.current = true
    setSubmitting(true)
    try {
      const payload = JSON.parse(await file.text())
      if (payload?.format !== 'process-diagram/v1' || !Array.isArray(payload.nodes) || !Array.isArray(payload.edges)) {
        throw new Error('请选择从流程图中导出的 JSON 文件；数据备份请在数据关系总图中恢复')
      }
      const created = await useProcessStore.getState().create({
        name: payload.name, description: payload.description || '', nodes: payload.nodes, edges: payload.edges,
      })
      navigation.current += 1
      useStore.getState().select(null)
      setActive(created.id)
      setDirectoryOpen(false)
      message.success('已导入为新的流程图')
    } finally { mutationLock.current = false; setSubmitting(false) }
  }

  const activeSummary = active === 'data' ? null : diagrams.find((d) => d.id === active)
  const documentName = active === 'data' ? '数据关系总图'
    : current?.id === active ? current.name : activeSummary?.name || '流程图'

  return (
    <div className="workspace-shell">
      <header className="workspace-topbar">
        <div className="workspace-brand"><ApartmentOutlined aria-hidden="true" /><span>流程与数据</span></div>
        <Button type="text" className="workspace-directory-trigger" icon={<FolderOpenOutlined />} disabled={locked} onClick={() => { setDirectoryOpen(true); void refreshDirectory() }}>图目录</Button>
        <span className="workspace-topbar-divider" />
        <div className="workspace-document-title" title={documentName}>
          {active === 'data' ? <DatabaseOutlined aria-hidden="true" /> : <ApartmentOutlined aria-hidden="true" />}
          <h1>{documentName}</h1>
        </div>
        {active !== 'data' && <span className={`workspace-save-status${error ? ' has-error' : ''}`} role="status">{busy ? '正在保存…' : loading ? '正在加载…' : error ? <Button type="link" size="small" disabled={locked} onClick={() => void openProcess(active)}>重新加载</Button> : '已保存'}</span>}
        <div className="workspace-topbar-spacer" />
        {activeSummary && <Dropdown trigger={['click']} menu={{ items: [
          { key: 'rename', label: '编辑流程信息', icon: <EditOutlined />, disabled: locked, onClick: () => editDocument(activeSummary) },
          { key: 'delete', label: '删除流程图', icon: <DeleteOutlined />, danger: true, disabled: locked, onClick: () => removeDocument(activeSummary) },
        ] }}><Button type="text" icon={<MoreOutlined />} aria-label="流程图管理" disabled={locked} /></Dropdown>}
        <Button type="primary" icon={<PlusOutlined />} disabled={locked} onClick={() => editDocument('new')}>新建流程</Button>
      </header>

      <main className="workspace-main">
        {active === 'data' ? (
          <ReactFlowProvider key="data"><DataWorkspace onOpenProcess={(id) => void openProcess(id)} /></ReactFlowProvider>
        ) : current?.id === active ? (
          <ReactFlowProvider key={active}><ProcessEditor onOpenAsset={(id) => void openAsset(id)} /></ReactFlowProvider>
        ) : (
          <div className="workspace-loading">
            {loading ? <Spin tip="正在打开流程图"><div style={{ width: 240, height: 80 }} /></Spin> : (
              <Alert type="error" showIcon message="流程图未能打开" description={error || '流程图可能已被删除，请重新打开图目录。'} action={<Button onClick={() => void openProcess(active)}>重试</Button>} />
            )}
          </div>
        )}
      </main>

      <Drawer className="workspace-directory" title="图目录" placement="left" width={320} open={directoryOpen} onClose={() => setDirectoryOpen(false)}>
        <p className="workspace-directory-hint">选择要梳理的内容</p>
        <button className={`workspace-document-row${active === 'data' ? ' is-active' : ''}`} disabled={locked} onClick={openData}>
          <DatabaseOutlined aria-hidden="true" /><span><strong>数据关系总图</strong><small>系统关系、业务关联与数据血缘</small></span>
        </button>
        <div className="workspace-section-heading"><h2>流程图 <span>{diagrams.length}</span></h2><Button type="text" size="small" icon={<PlusOutlined />} aria-label="新建流程图" disabled={locked} onClick={() => editDocument('new')} /></div>
        {directoryError && <Alert type="error" showIcon message="目录加载失败" description={directoryError} action={<Button size="small" onClick={() => void refreshDirectory()}>重试</Button>} />}
        {!directoryError && diagrams.length === 0 && <div className="workspace-empty-directory"><p>还没有流程图</p><span>从空白画布开始，或使用审批示例。</span><Button onClick={() => editDocument('new')} disabled={locked}>创建第一张流程图</Button></div>}
        <div className="workspace-document-list">
          {diagrams.map((document) => <div className="workspace-document-list-item" key={document.id}>
            <button className={`workspace-document-row${active === document.id ? ' is-active' : ''}`} disabled={locked} onClick={() => void openProcess(document.id)}>
              <ApartmentOutlined aria-hidden="true" /><span><strong>{document.name}</strong><small>{document.nodeCount} 个步骤 · {document.edgeCount} 条连线</small></span>
            </button>
            <Dropdown trigger={['click']} menu={{ items: [
              { key: 'rename', label: '编辑流程信息', icon: <EditOutlined />, onClick: () => editDocument(document), disabled: locked },
              { key: 'delete', label: '删除流程图', icon: <DeleteOutlined />, danger: true, onClick: () => removeDocument(document), disabled: locked },
            ] }}><Button className="workspace-document-menu" type="text" icon={<MoreOutlined />} aria-label={`管理${document.name}`} disabled={locked} /></Dropdown>
          </div>)}
        </div>
        <div className="workspace-directory-import">
          <Upload accept=".json" showUploadList={false} disabled={locked} customRequest={async ({ file, onSuccess, onError }) => {
            try { await importFlow(file as File); onSuccess?.({}) }
            catch (e) { message.error(`导入失败：${(e as Error).message}`); onError?.(e as Error) }
          }}><Button icon={<ImportOutlined />} disabled={locked}>导入流程文件</Button></Upload>
          <p>每张流程图独立保存，可共享引用已有资产。</p>
        </div>
      </Drawer>

      <Modal title={editing === 'new' ? '新建流程图' : '编辑流程信息'} open={editing !== null} onCancel={() => !submitting && setEditing(null)} onOk={() => void saveDocument()} okText={editing === 'new' ? '创建流程图' : '保存'} confirmLoading={submitting} destroyOnHidden>
        <Form form={form} layout="vertical" preserve={false} style={{ marginTop: 24 }}>
          <Form.Item name="name" label="流程名称" rules={[{ required: true, whitespace: true, message: '请输入流程名称' }, { max: 200, message: '名称最多 200 字' }]}><Input autoFocus placeholder="例如：采购申请流程" maxLength={200} /></Form.Item>
          <Form.Item name="description" label="流程说明"><Input.TextArea placeholder="描述这条流程的目的和适用范围" maxLength={4000} rows={3} /></Form.Item>
          {editing === 'new' && <Form.Item name="template" label="起始画布"><Radio.Group className="workspace-template-options"><Radio value="blank">空白流程</Radio><Radio value="approval">申请审批示例</Radio></Radio.Group></Form.Item>}
        </Form>
      </Modal>
    </div>
  )
}
