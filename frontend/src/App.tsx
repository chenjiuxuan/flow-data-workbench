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
import { nodeBounds } from './utils/bounds'
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
  const initialize = useStore((s) => s.initialize)
  const loading = useStore((s) => s.loading)
  const busy = useStore((s) => s.busy)
  const fitVersion = useStore((s) => s.fitVersion)
  const canUndo = useStore((s) => s.past.length > 0)
  const canRedo = useStore((s) => s.future.length > 0)
  const undo = useCallback(async () => {
    try {
      if (await useStore.getState().undo()) message.success('已撤销并保存')
    } catch (error) {
      message.error(`撤销失败：${(error as Error).message}`)
    }
  }, [message])
  const redo = useCallback(async () => {
    try {
      if (await useStore.getState().redo()) message.success('已重做并保存')
    } catch (error) {
      message.error(`重做失败：${(error as Error).message}`)
    }
  }, [message])
  const [importOpen, setImportOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const fittedRef = useRef<{ view: ViewKey; version: number } | null>(null)

  const fitSoon = useCallback((delay = 150) => {
    setTimeout(() => fitView({ duration: 300, padding: 0.15 }), delay)
  }, [fitView])

  // 显式布局与拖动使用同一原子编辑入口，可持久化撤销。
  const applyPositions = useCallback(
    async (positions: Record<string, { x: number; y: number }>) => {
      try {
        await useStore.getState().saveLayout(positions)
        fitSoon()
        message.success('已自动布局')
      } catch (error) {
        message.error(`布局失败：${(error as Error).message}`)
      }
    },
    [fitSoon, message],
  )

  const relayoutAll = useCallback(
    () => applyPositions(autoLayout({
      nodeIds: nodes.map((n) => n.id),
      edges: edges.map((e) => ({ source: e.source, target: e.target })),
    })),
    [applyPositions, nodes, edges],
  )

  const onLayoutClick = useCallback(() => void relayoutAll(), [relayoutAll])

  useEffect(() => {
    void initialize().catch((error: Error) => message.error(`加载失败：${error.message}`))
  }, [initialize, message])

  // 等新视图数据真正到达后再适配；导入的新节点也会进入视野。
  useEffect(() => {
    if (nodes.length > 0 && (fittedRef.current?.view !== view || fittedRef.current.version !== fitVersion)) {
      fittedRef.current = { view, version: fitVersion }
      fitSoon()
    }
  }, [nodes.length, view, fitVersion, fitSoon])

  // 撤销/重做/清空：Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      // 输入框/文本框内不拦截
      const tag = (event.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (event.target as HTMLElement).isContentEditable) return
      if (useStore.getState().busy) return
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        void redo()
        return
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        void undo()
        return
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
        event.preventDefault()
        void redo()
        return
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [undo, redo])

  const seedDemo = useCallback(async () => {
    try {
      await useStore.getState().runExternal(api.seedDemo, { view: () => 'system' })
      fitSoon()
      message.success('演示数据已就绪')
    } catch (error) {
      message.error(`演示数据加载失败：${(error as Error).message}`)
    }
  }, [fitSoon, message])

  const exportPng = async () => {
    const viewportEl = wrapperRef.current?.querySelector('.react-flow__viewport') as HTMLElement | null
    if (!viewportEl || nodes.length === 0) return
    // 按节点实际范围计算导出画布，覆盖视口的平移缩放，避免导出被裁切
    const bounds = nodeBounds(nodes)!
    const minX = bounds.minX - 40
    const minY = bounds.minY - 40
    const boundsW = bounds.maxX - bounds.minX + NODE_W + 80
    const boundsH = bounds.maxY - bounds.minY + NODE_H + 80
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
          onChange={(v) => void setView(v as ViewKey).catch((error: Error) => message.error(`视图加载失败：${error.message}`))}
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
            <Button size="small" disabled={!canUndo || busy} icon={<UndoOutlined />} onClick={() => void undo()}>撤销</Button>
          </Tooltip>
          <Tooltip title="重做（Ctrl+Shift+Z 或 Ctrl+Y）">
            <Button size="small" disabled={!canRedo || busy} icon={<RedoOutlined />} onClick={() => void redo()}>重做</Button>
          </Tooltip>
          <Divider type="vertical" style={{ height: 20 }} />
          <Tooltip title="按流向自动排列">
            <Button size="small" disabled={busy || !nodes.length} icon={<LayoutOutlined />} onClick={onLayoutClick}>自动布局</Button>
          </Tooltip>
          <Button size="small" type="primary" disabled={busy} icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>导入 / 采集</Button>
          <Tooltip title="导出当前视图为 PNG">
            <Button size="small" icon={<DownloadOutlined />} onClick={() => void exportPng()}>导出图片</Button>
          </Tooltip>
          <Upload
            accept=".json"
            showUploadList={false}
            disabled={busy}
            customRequest={async ({ file, onSuccess, onError }) => {
              try {
                const text = await (file as File).text()
                const payload = JSON.parse(text)
                await useStore.getState().runExternal(() => api.restoreJson({ ...payload, mode: 'merge' }), { layout: false })
                onSuccess?.({})
                message.success('备份已恢复')
              } catch (e) {
                onError?.(e as Error)
                message.error(`恢复失败：${(e as Error).message}`)
              }
            }}
          >
            <Button size="small" disabled={busy} icon={<ReloadOutlined />}>恢复备份</Button>
          </Upload>
          <Tooltip title="重新并入内置演示数据（幂等，不影响你的修改）">
            <Button
              size="small"
              disabled={busy}
              icon={<ThunderboltOutlined />}
              onClick={() => void seedDemo()}
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
                <Button type="link" disabled={busy} size="small" onClick={() => void seedDemo()}>
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
