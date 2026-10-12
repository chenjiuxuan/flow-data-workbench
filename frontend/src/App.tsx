/** 页面骨架：顶栏（视图切换/布局/导入/导出）+ 左侧面板 + 画布 + 属性面板。 */
import {
  DownloadOutlined, ImportOutlined, LayoutOutlined, ReloadOutlined, ThunderboltOutlined,
  UndoOutlined, RedoOutlined,
} from '@ant-design/icons'
import { App as AntdApp, Button, Divider, Segmented, Space, Tooltip, Upload } from 'antd'
import { toPng } from 'html-to-image'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useReactFlow } from '@xyflow/react'
import { api } from './api/client'
import { FlowCanvas } from './components/FlowCanvas'
import { ImportModal } from './components/ImportModal'
import { NodePalette } from './components/NodePalette'
import { PropertyPanel } from './components/PropertyPanel'
import { VIEW_TITLE } from './components/meta'
import { useStore } from './store'
import { autoLayout } from './utils/layout'
import type { ViewKey } from './types'

const NODE_W = 200
const NODE_H = 60

function TraceBanner() {
  const trace = useStore((s) => s.trace)
  const clearTrace = useStore((s) => s.clearTrace)
  if (!trace) return null
  const label = trace.direction === 'upstream' ? '上游来源' : trace.direction === 'downstream' ? '下游去向' : '关联链路'
  return (
    <div
      style={{
        position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)', zIndex: 10,
        background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 8,
        padding: '6px 14px', fontSize: 13, boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      }}
    >
      追溯「{trace.nodeName}」的{label}：{trace.nodeIds.size} 个节点 / {trace.edgeIds.size} 条链路，
      未在链路中的节点已淡化。
      <Button type="link" size="small" onClick={clearTrace}>清除</Button>
    </div>
  )
}

export default function App() {
  const { message } = AntdApp.useApp()
  const { fitView } = useReactFlow()
  const view = useStore((s) => s.view)
  const nodes = useStore((s) => s.nodes)
  const edges = useStore((s) => s.edges)
  const setView = useStore((s) => s.setView)
  const refresh = useStore((s) => s.refresh)
  const loading = useStore((s) => s.loading)
  // undo/redo 后让 ReactFlow 重新 fitView（节点位置变了）
  const [, setUndoRedoTick] = useState(0)
  const undo = useCallback(() => {
    useStore.temporal.getState().undo()
    setUndoRedoTick((t) => t + 1)
    setTimeout(() => fitView({ duration: 300, padding: 0.15 }), 50)
    message.info('已撤销')
  }, [message, fitView])
  const redo = useCallback(() => {
    useStore.temporal.getState().redo()
    setUndoRedoTick((t) => t + 1)
    setTimeout(() => fitView({ duration: 300, padding: 0.15 }), 50)
    message.info('已重做')
  }, [message, fitView])
  const [importOpen, setImportOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const hadNodesRef = useRef(false)

  const fitSoon = useCallback((delay = 150) => {
    setTimeout(() => fitView({ duration: 300, padding: 0.15 }), delay)
  }, [fitView])

  // 把一组新位置写回 store 与后端，然后重新适配视野
  const applyPositions = useCallback(
    async (positions: Record<string, { x: number; y: number }>, silent = false) => {
      useStore.setState({
        nodes: nodes.map((n) => ({ ...n, x: positions[n.id]?.x ?? n.x, y: positions[n.id]?.y ?? n.y })),
      })
      const payload = Object.fromEntries(
        Object.entries(positions).map(([id, p]) => [id, [p.x, p.y] as [number, number]]),
      )
      await api.saveLayout(payload)
      fitSoon()
      if (!silent) message.success('已自动布局')
    },
    [nodes, fitSoon, message],
  )

  const relayoutAll = useCallback(
    () => applyPositions(autoLayout({
      nodeIds: nodes.map((n) => n.id),
      edges: edges.map((e) => ({ source: e.source, target: e.target })),
    })),
    [applyPositions, nodes, edges],
  )

  const onLayoutClick = useCallback(() => void relayoutAll(), [relayoutAll])

  // 初始加载与视图切换后重新适配视野
  useEffect(() => {
    void (async () => {
      if (!hadNodesRef.current && nodes.length === 0) {
        await setView('system')
      }
      if (nodes.length > 0 && !hadNodesRef.current) {
        hadNodesRef.current = true
        fitSoon()
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes.length])

  useEffect(() => {
    if (hadNodesRef.current) fitSoon(100)
  }, [view, fitSoon])

  // 撤销/重做/清空：Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      // 输入框/文本框内不拦截
      const tag = (event.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (event.target as HTMLElement).isContentEditable) return
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        redo()
        return
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        undo()
        return
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
        event.preventDefault()
        redo()
        return
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [undo, redo])

  // 清空撤销栈：演示数据/JSON 恢复本身不可撤销
  useEffect(() => {
    useStore.temporal.getState().clear()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 堆叠检测：自动采集/导入产生的节点没有坐标，会堆在 (0,0)。
  // 大部分节点都堆着 → 整图自动布局；只是新增的少数节点堆着 → 排到现有内容右侧一列。
  useEffect(() => {
    if (nodes.length < 2) return
    const piled = nodes.filter((n) => n.x === 0 && n.y === 0)
    if (piled.length < 2) return

    if (piled.length >= nodes.length / 2) {
      void relayoutAll()
      return
    }
    const maxX = Math.max(...nodes.map((n) => n.x))
    const positions: Record<string, { x: number; y: number }> = {}
    piled.forEach((n, i) => {
      positions[n.id] = { x: maxX + NODE_W + 120, y: 80 + i * (NODE_H + 50) }
    })
    void applyPositions(positions, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes])

  const exportPng = async () => {
    const viewportEl = wrapperRef.current?.querySelector('.react-flow__viewport') as HTMLElement | null
    if (!viewportEl || nodes.length === 0) return
    // 按节点实际范围计算导出画布，覆盖视口的平移缩放，避免导出被裁切
    const xs = nodes.map((n) => n.x)
    const ys = nodes.map((n) => n.y)
    const minX = Math.min(...xs) - 40
    const minY = Math.min(...ys) - 40
    const boundsW = Math.max(...xs) - Math.min(...xs) + NODE_W + 80
    const boundsH = Math.max(...ys) - Math.min(...ys) + NODE_H + 80
    const scale = Math.min(2, Math.max(0.4, 1600 / boundsW))
    const width = Math.max(1, Math.ceil(boundsW * scale))
    const height = Math.max(1, Math.ceil(boundsH * scale))
    try {
      const dataUrl = await toPng(viewportEl, {
        backgroundColor: '#f7f8fa',
        pixelRatio: 2,
        width,
        height,
        style: {
          width: `${width}px`,
          height: `${height}px`,
          transform: `translate(${-minX * scale}px, ${-minY * scale}px) scale(${scale})`,
        },
      })
      const link = document.createElement('a')
      link.download = `数据流图-${view}-${new Date().toISOString().slice(0, 10)}.png`
      link.href = dataUrl
      link.click()
      message.success('已导出 PNG')
    } catch (e) {
      message.error(`导出失败：${(e as Error).message}`)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#fafafa' }}>
      {/* 顶栏 */}
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '10px 20px',
          borderBottom: '1px solid #f0f0f0', background: '#fff', flexWrap: 'wrap',
          boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 16, color: '#1677ff' }}> 数据流梳理平台</div>
        <Segmented
          value={view}
          onChange={(v) => void setView(v as ViewKey)}
          options={[
            { value: 'system', label: '系统视图' },
            { value: 'business', label: '业务视图' },
            { value: 'field', label: '字段视图' },
          ]}
        />
        <span style={{ fontSize: 12, color: '#999', maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {VIEW_TITLE[view]}
        </span>
        <div style={{ flex: 1 }} />
        <Space size="small">
          <Tooltip title="撤销上一步（Ctrl+Z）">
            <Button size="small" icon={<UndoOutlined />} onClick={() => undo()}>撤销</Button>
          </Tooltip>
          <Tooltip title="重做（Ctrl+Shift+Z 或 Ctrl+Y）">
            <Button size="small" icon={<RedoOutlined />} onClick={() => redo()}>重做</Button>
          </Tooltip>
          <Divider type="vertical" style={{ height: 20 }} />
          <Tooltip title="按流向自动排列">
            <Button size="small" icon={<LayoutOutlined />} onClick={onLayoutClick}>自动布局</Button>
          </Tooltip>
          <Button size="small" type="primary" icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>导入 / 采集</Button>
          <Tooltip title="导出当前视图为 PNG">
            <Button size="small" icon={<DownloadOutlined />} onClick={() => void exportPng()}>导出图片</Button>
          </Tooltip>
          <Upload
            accept=".json"
            showUploadList={false}
            customRequest={async ({ file }) => {
              try {
                const text = await (file as File).text()
                const payload = JSON.parse(text)
                await api.restoreJson({ ...payload, mode: 'merge' })
                await refresh()
                message.success('备份已恢复')
              } catch (e) {
                message.error(`恢复失败：${(e as Error).message}`)
              }
            }}
          >
            <Button size="small" icon={<ReloadOutlined />}>恢复备份</Button>
          </Upload>
          <Tooltip title="重新并入内置演示数据（幂等，不影响你的修改）">
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={async () => {
                await api.seedDemo()
                await refresh()
                message.success('演示数据已就绪')
              }}
            >
              演示数据
            </Button>
          </Tooltip>
        </Space>
      </div>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <div style={{ width: 220, padding: 0, borderRight: '1px solid #f0f0f0', background: '#fafafa', overflowY: 'auto' }}>
          <NodePalette />
        </div>
        <div ref={wrapperRef} style={{ flex: 1, position: 'relative', background: '#f7f8fa' }}>
          <FlowCanvas />
          <TraceBanner />
          {nodes.length === 0 && (
            <div
              style={{
                position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                textAlign: 'center', color: '#999', pointerEvents: 'none',
              }}
            >
              <div style={{ fontSize: 48, marginBottom: 16 }}>🎨</div>
              <div style={{ fontSize: 16, marginBottom: 8, color: '#666' }}>画布还是空的</div>
              <div style={{ fontSize: 13 }}>
                从左侧拖拽节点到画布，或点击
                <Button type="link" size="small" onClick={async () => {
                  await api.seedDemo()
                  await refresh()
                  message.success('演示数据已就绪')
                }}>
                  演示数据
                </Button>
                快速体验
              </div>
            </div>
          )}
        </div>
      </div>

      <PropertyPanel />
      <ImportModal open={importOpen} onClose={() => setImportOpen(false)} />
      {loading && (
        <div style={{ position: 'fixed', top: 4, right: 8, fontSize: 12, color: '#999' }}>加载中…</div>
      )}
      <GraphNodeCount />
    </div>
  )
}

function GraphNodeCount() {
  const view = useStore((s) => s.view)
  const nodes = useStore((s) => s.nodes)
  const edges = useStore((s) => s.edges)
  return (
    <span style={{ position: 'fixed', bottom: 8, right: 12, fontSize: 12, color: '#bbb' }}>
      {view} · {nodes.length} 节点 / {edges.length} 连线
    </span>
  )
}
