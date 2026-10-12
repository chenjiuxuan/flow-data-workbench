import {
  Background, BackgroundVariant, Controls, MarkerType, ReactFlow, SelectionMode,
  applyEdgeChanges, applyNodeChanges, useReactFlow,
  type Connection, type Edge as FlowEdge, type EdgeChange, type NodeChange,
} from '@xyflow/react'
import {
  AimOutlined, DownloadOutlined, FileImageOutlined, FileTextOutlined,
  LayoutOutlined, RedoOutlined, UndoOutlined,
} from '@ant-design/icons'
import { App as AntdApp, Button, Dropdown, Spin, Tooltip } from 'antd'
import dagre from '@dagrejs/dagre'
import { toBlob } from 'html-to-image'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ProcessNode, type ProcessFlowNode } from './components/ProcessNode'
import { ProcessPalette } from './components/ProcessPalette'
import { ProcessProperties } from './components/ProcessProperties'
import { PROCESS_NODE_META, PROCESS_NODE_TYPES } from './meta'
import { useProcessStore } from './store'
import type { ProcessBackup, ProcessNode as NodeRecord, ProcessNodeType } from './types'
import './process.css'

const nodeTypes = { processNode: ProcessNode }
const EMPTY_NODES: NodeRecord[] = []
const nextId = (prefix: string) => `${prefix}:${crypto.randomUUID()}`
const fileName = (name: string) => name.replace(/[<>:"/\\|?*\x00-\x1F]/g, '_').slice(0, 100)

function inTextOrDialog(target: EventTarget | null) {
  return target instanceof HTMLElement && !!target.closest('input, textarea, select, [contenteditable="true"], [role="dialog"], [role="combobox"], [role="textbox"], [role="listbox"]')
}

export function ProcessEditor({ onOpenAsset }: { onOpenAsset: (assetId: string) => void }) {
  const { message } = AntdApp.useApp()
  const { screenToFlowPosition, fitView } = useReactFlow<ProcessFlowNode, FlowEdge>()
  const current = useProcessStore((state) => state.current)
  const busy = useProcessStore((state) => state.busy)
  const loading = useProcessStore((state) => state.loading)
  const fitVersion = useProcessStore((state) => state.fitVersion)
  const canUndo = useProcessStore((state) => state.past.length > 0)
  const canRedo = useProcessStore((state) => state.future.length > 0)
  const selection = useProcessStore((state) => state.selection)
  const nodes = current?.nodes ?? EMPTY_NODES
  const [flowNodes, setFlowNodes] = useState<ProcessFlowNode[]>([])
  const [flowEdges, setFlowEdges] = useState<FlowEdge[]>([])
  const [assetLoading, setAssetLoading] = useState(true)
  const [assetError, setAssetError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const selectedRef = useRef<{ nodes: string[]; edges: string[] }>({ nodes: [], edges: [] })
  const pendingSelect = useRef<string | null>(null)
  const fittedVersion = useRef<number | null>(null)
  const fitTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const assetRequest = useRef(0)
  const readOnly = busy || loading

  const fitSoon = useCallback(() => {
    if (fitTimer.current) clearTimeout(fitTimer.current)
    fitTimer.current = setTimeout(() => void fitView({ padding: 0.2, duration: 220, maxZoom: 1 }), 120)
  }, [fitView])

  useEffect(() => () => {
    if (fitTimer.current) clearTimeout(fitTimer.current)
    assetRequest.current += 1
  }, [])

  const refreshAssets = useCallback(async () => {
    const request = ++assetRequest.current
    setAssetLoading(true)
    setAssetError(null)
    try {
      await useProcessStore.getState().loadAssets()
    } catch (error) {
      if (request === assetRequest.current) setAssetError((error as Error).message)
    } finally {
      if (request === assetRequest.current) setAssetLoading(false)
    }
  }, [])

  useEffect(() => { void refreshAssets() }, [refreshAssets])

  const displayNodes = useMemo((): ProcessFlowNode[] => nodes.map((node) => ({
    id: node.id,
    type: 'processNode',
    position: { x: node.x, y: node.y },
    width: PROCESS_NODE_META[node.type].width,
    height: PROCESS_NODE_META[node.type].height,
    data: { node },
  })), [nodes])

  useEffect(() => {
    const added = pendingSelect.current
    setFlowNodes((previous) => displayNodes.map((node) => {
      const old = previous.find((item) => item.id === node.id)
      return {
        ...old, ...node,
        position: old?.dragging ? old.position : node.position,
        selected: added ? node.id === added : old?.selected ?? false,
      }
    }))
    pendingSelect.current = null
  }, [displayNodes])

  useEffect(() => {
    setFlowEdges((previous) => (current?.edges ?? []).map((edge): FlowEdge => ({
      ...edge,
      // 无句柄信息的旧导入数据也映射到实际可见出口。
      sourceHandle: edge.sourceHandle ?? (nodes.find((node) => node.id === edge.source)?.type === 'decision' ? 'yes' : 'out'),
      targetHandle: edge.targetHandle ?? 'in',
      type: 'smoothstep',
      selected: previous.find((item) => item.id === edge.id)?.selected ?? false,
      markerEnd: { type: MarkerType.ArrowClosed, color: '#7c8da5', width: 18, height: 18 },
      style: { stroke: '#7c8da5', strokeWidth: 1.6 },
      labelStyle: { fill: '#475569', fontSize: 12 },
      labelBgStyle: { fill: '#fff', fillOpacity: 0.96 },
      labelBgPadding: [7, 4],
      labelBgBorderRadius: 4,
    })))
  }, [current?.edges, nodes])

  useEffect(() => {
    if (nodes.length && fittedVersion.current !== fitVersion) {
      fittedVersion.current = fitVersion
      fitSoon()
    }
  }, [nodes.length, fitVersion, fitSoon])

  const save = useCallback(async (operation: () => Promise<boolean>, label: string) => {
    try {
      const changed = await operation()
      if (changed && label) message.success(label)
      return changed
    } catch (error) {
      message.error(`保存失败：${(error as Error).message}`)
      return false
    }
  }, [message])

  const undo = useCallback(() => save(() => useProcessStore.getState().undo(), '已撤销并保存'), [save])
  const redo = useCallback(() => save(() => useProcessStore.getState().redo(), '已重做并保存'), [save])
  const deleteItems = useCallback(async (nodeIds: string[], edgeIds: string[]) => {
    const state = useProcessStore.getState()
    if (state.busy || state.loading || !nodeIds.length && !edgeIds.length) return
    const removed = new Set(nodeIds)
    const removedEdges = new Set(edgeIds)
    await save(() => state.edit((draft) => {
      draft.nodes = draft.nodes.filter((node) => !removed.has(node.id))
      draft.edges = draft.edges.filter((edge) => !removedEdges.has(edge.id) && !removed.has(edge.source) && !removed.has(edge.target))
    }), '已删除选中项，可撤销')
  }, [save])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.defaultPrevented || inTextOrDialog(event.target) || useProcessStore.getState().busy || useProcessStore.getState().loading) return
      const key = event.key.toLowerCase()
      if ((event.ctrlKey || event.metaKey) && key === 'z') {
        event.preventDefault()
        void (event.shiftKey ? redo() : undo())
      } else if ((event.ctrlKey || event.metaKey) && key === 'y') {
        event.preventDefault()
        void redo()
      } else if (event.key === 'Delete' || event.key === 'Backspace') {
        const selected = selectedRef.current
        if (selected.nodes.length || selected.edges.length) {
          event.preventDefault()
          void deleteItems(selected.nodes, selected.edges)
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [deleteItems, redo, undo])

  const addNode = useCallback(async (type: ProcessNodeType, position?: { x: number; y: number }) => {
    const state = useProcessStore.getState()
    if (!state.current || state.busy || state.loading) return
    if (state.current.nodes.length >= 500) { message.warning('每张流程最多支持 500 个节点'); return }
    const bounds = wrapperRef.current?.getBoundingClientRect()
    const center = position ?? screenToFlowPosition({
      x: (bounds?.left ?? 0) + (bounds?.width ?? 600) / 2,
      y: (bounds?.top ?? 0) + (bounds?.height ?? 400) / 2,
    })
    const meta = PROCESS_NODE_META[type]
    const id = nextId('step')
    const index = state.current.nodes.filter((node) => node.type === type).length + 1
    let placement = { x: center.x - meta.width / 2, y: center.y - meta.height / 2 }
    if (!position) {
      // 点击添加从视口中心向外寻找空位；按最大节点尺寸留出连线与拖动空间。
      const origin = placement
      const gap = 36
      const stepX = 180 + gap
      const stepY = 104 + gap
      const vacant = (candidate: { x: number; y: number }) => state.current!.nodes.every((existing) => {
        const size = PROCESS_NODE_META[existing.type]
        return candidate.x + meta.width + gap <= existing.x || candidate.x >= existing.x + size.width + gap
          || candidate.y + meta.height + gap <= existing.y || candidate.y >= existing.y + size.height + gap
      })
      let found = false
      for (let ring = 0; ring <= state.current.nodes.length + 1 && !found; ring += 1) {
        const offsets: { x: number; y: number }[] = []
        for (let row = -ring; row <= ring; row += 1) {
          for (let col = -ring; col <= ring; col += 1) {
            if (Math.max(Math.abs(col), Math.abs(row)) === ring) offsets.push({ x: col, y: row })
          }
        }
        // 同一圈内先找最近空位，相同距离优先右侧、下方。
        offsets.sort((a, b) => a.x * a.x + a.y * a.y - b.x * b.x - b.y * b.y || b.x - a.x || b.y - a.y)
        for (const offset of offsets) {
          const candidate = { x: origin.x + offset.x * stepX, y: origin.y + offset.y * stepY }
          if (vacant(candidate)) { placement = candidate; found = true; break }
        }
      }
    }
    const node: NodeRecord = {
      id, type, name: index === 1 ? meta.label : `${meta.label} ${index}`,
      x: placement.x, y: placement.y,
      owner: '', description: '', input: '', output: '', assetIds: [],
    }
    pendingSelect.current = id
    const changed = await save(() => state.edit((draft) => { draft.nodes.push(node) }), '')
    if (changed) {
      useProcessStore.getState().select({ kind: 'node', id })
      if (!position) requestAnimationFrame(() => {
        // 属性面板展开后再检查新的可用视口，空位超出画布时才重新适配。
        const canvas = wrapperRef.current?.getBoundingClientRect()
        if (!canvas) return
        const topLeft = screenToFlowPosition({ x: canvas.left + 24, y: canvas.top + 24 })
        const bottomRight = screenToFlowPosition({ x: canvas.right - 24, y: canvas.bottom - 24 })
        if (node.x < topLeft.x || node.y < topLeft.y || node.x + meta.width > bottomRight.x || node.y + meta.height > bottomRight.y) fitSoon()
      })
    } else pendingSelect.current = null
  }, [fitSoon, message, save, screenToFlowPosition])

  const validConnection = useCallback((connection: Connection | FlowEdge) => {
    const state = useProcessStore.getState()
    if (state.busy || state.loading || !state.current || !connection.source || !connection.target || connection.source === connection.target) return false
    const source = state.current.nodes.find((node) => node.id === connection.source)
    const target = state.current.nodes.find((node) => node.id === connection.target)
    if (!source || !target || source.type === 'end' || target.type === 'start') return false
    const allowed = source.type === 'decision' ? ['yes', 'no'] : ['out']
    return allowed.includes(connection.sourceHandle ?? 'out') && (connection.targetHandle === 'in' || !connection.targetHandle)
  }, [])

  const connect = useCallback(async (connection: Connection) => {
    if (!validConnection(connection)) return
    const state = useProcessStore.getState()
    if (state.current!.edges.length >= 2000) { message.warning('每张流程最多支持 2000 条连线'); return }
    if (state.current!.edges.some((edge) => edge.source === connection.source && edge.target === connection.target && (edge.sourceHandle ?? 'out') === connection.sourceHandle)) {
      message.info('这两个节点之间已存在相同连线')
      return
    }
    const id = nextId('link')
    await save(() => state.edit((draft) => { draft.edges.push({
      id, source: connection.source, target: connection.target,
      sourceHandle: connection.sourceHandle, targetHandle: connection.targetHandle ?? 'in',
      label: connection.sourceHandle === 'yes' ? '是' : connection.sourceHandle === 'no' ? '否' : '',
    }) }), '')
  }, [message, save, validConnection])

  const layout = async () => {
    if (readOnly || !nodes.length) return
    const graph = new dagre.graphlib.Graph()
    graph.setGraph({ rankdir: 'LR', nodesep: 70, ranksep: 110, marginx: 50, marginy: 50, acyclicer: 'greedy' })
    graph.setDefaultEdgeLabel(() => ({}))
    for (const node of nodes) graph.setNode(node.id, { width: PROCESS_NODE_META[node.type].width, height: PROCESS_NODE_META[node.type].height })
    for (const edge of current!.edges) graph.setEdge(edge.source, edge.target)
    dagre.layout(graph)
    const positions = new Map(nodes.map((node) => {
      const position = graph.node(node.id)
      const meta = PROCESS_NODE_META[node.type]
      return [node.id, { x: position.x - meta.width / 2, y: position.y - meta.height / 2 }] as const
    }))
    if (await save(() => useProcessStore.getState().edit((draft) => {
      draft.nodes.forEach((node) => Object.assign(node, positions.get(node.id)))
    }), '已整理布局')) fitSoon()
  }

  const exportJson = () => {
    if (!current) return
    const backup: ProcessBackup = { format: 'process-diagram/v1', name: current.name, description: current.description, nodes: current.nodes, edges: current.edges }
    const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${fileName(current.name)}.json`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    message.success('已导出当前流程 JSON')
  }

  const exportPng = async () => {
    const viewport = wrapperRef.current?.querySelector<HTMLElement>('.react-flow__viewport')
    if (!viewport || !current || !nodes.length) return
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const node of nodes) {
      const meta = PROCESS_NODE_META[node.type]
      minX = Math.min(minX, node.x); minY = Math.min(minY, node.y)
      maxX = Math.max(maxX, node.x + meta.width); maxY = Math.max(maxY, node.y + meta.height)
    }
    for (const edge of viewport.querySelectorAll<SVGGraphicsElement>('.react-flow__edge-path')) {
      const bounds = edge.getBBox()
      minX = Math.min(minX, bounds.x); minY = Math.min(minY, bounds.y)
      maxX = Math.max(maxX, bounds.x + bounds.width); maxY = Math.max(maxY, bounds.y + bounds.height)
    }
    minX -= 64; minY -= 64; maxX += 64; maxY += 64
    const scale = Math.min(2, 1600 / (maxX - minX), 1200 / (maxY - minY))
    const width = Math.max(320, Math.ceil((maxX - minX) * scale))
    const height = Math.max(200, Math.ceil((maxY - minY) * scale))
    setExporting(true)
    try {
      const blob = await toBlob(viewport, {
        backgroundColor: '#f8fafc', width, height, pixelRatio: 2,
        style: { width: `${width}px`, height: `${height}px`, transform: `translate(${-minX * scale}px, ${-minY * scale}px) scale(${scale})` },
        filter: (element) => !(element instanceof HTMLElement && element.classList.contains('react-flow__selection')),
      })
      if (!blob) throw new Error('未能生成图片，请重试')
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.download = `${fileName(current.name)}.png`
      link.href = url
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      message.success('已导出当前流程 PNG')
    } catch (error) {
      message.error(`图片导出失败：${(error as Error).message}`)
    } finally { setExporting(false) }
  }

  return (
    <div className="process-editor">
      <div className="process-toolbar" aria-label="流程编辑工具栏">
        <div className="process-toolbar__title"><span>{nodes.length} 个节点 · {current?.edges.length ?? 0} 条连线</span></div>
        <div className="process-toolbar__actions">
          <Tooltip title="撤销（Ctrl+Z）"><Button aria-label="撤销流程编辑" icon={<UndoOutlined />} disabled={readOnly || !canUndo} onClick={() => void undo()} /></Tooltip>
          <Tooltip title="重做（Ctrl+Y）"><Button aria-label="重做流程编辑" icon={<RedoOutlined />} disabled={readOnly || !canRedo} onClick={() => void redo()} /></Tooltip>
          <span className="process-toolbar__divider" />
          <Tooltip title="适应画布"><Button aria-label="适应流程画布" icon={<AimOutlined />} disabled={!nodes.length} onClick={fitSoon} /></Tooltip>
          <Button icon={<LayoutOutlined />} disabled={readOnly || !nodes.length} onClick={() => void layout()}>整理布局</Button>
          <Dropdown
            trigger={['click']}
            menu={{ items: [
              { key: 'png', label: '导出图片 PNG', icon: <FileImageOutlined />, disabled: !nodes.length },
              { key: 'json', label: '导出流程 JSON', icon: <FileTextOutlined /> },
            ], onClick: ({ key }) => { if (key === 'json') exportJson(); else void exportPng() } }}
          ><Button icon={<DownloadOutlined />} loading={exporting} disabled={readOnly || exporting}>导出</Button></Dropdown>
        </div>
      </div>
      <div className="process-editor__workspace">
        <ProcessPalette busy={readOnly} onAdd={(type) => void addNode(type)} />
        <div
          className="process-canvas"
          ref={wrapperRef}
          onDrop={(event) => {
            event.preventDefault()
            const type = event.dataTransfer.getData('application/process-node-type') as ProcessNodeType
            if (PROCESS_NODE_TYPES.includes(type)) void addNode(type, screenToFlowPosition({ x: event.clientX, y: event.clientY }))
          }}
          onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = readOnly ? 'none' : 'move' }}
        >
          <ReactFlow<ProcessFlowNode, FlowEdge>
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            onNodesChange={(changes: NodeChange<ProcessFlowNode>[]) => setFlowNodes((previous) => applyNodeChanges(changes, previous))}
            onEdgesChange={(changes: EdgeChange<FlowEdge>[]) => setFlowEdges((previous) => applyEdgeChanges(changes, previous))}
            onConnect={(connection) => void connect(connection)}
            isValidConnection={validConnection}
            nodesDraggable={!readOnly}
            nodesConnectable={!readOnly}
            selectionOnDrag
            selectionMode={SelectionMode.Partial}
            panOnDrag={[1, 2]}
            deleteKeyCode={null}
            minZoom={0.08}
            maxZoom={2}
            multiSelectionKeyCode="Shift"
            onSelectionChange={({ nodes: selectedNodes, edges: selectedEdges }) => { selectedRef.current = { nodes: selectedNodes.map((node) => node.id), edges: selectedEdges.map((edge) => edge.id) } }}
            onNodeClick={(_, node) => useProcessStore.getState().select({ kind: 'node', id: node.id })}
            onEdgeClick={(_, edge) => useProcessStore.getState().select({ kind: 'edge', id: edge.id })}
            onPaneClick={() => useProcessStore.getState().select(null)}
            onPaneContextMenu={(event) => event.preventDefault()}
            onNodeDragStop={async (_event, node, dragged) => {
              const state = useProcessStore.getState()
              if (state.busy || state.loading) return
              const moved = new Map((dragged.length ? dragged : [node]).map((item) => [item.id, item.position]))
              const changed = await save(() => state.edit((draft) => {
                for (const item of draft.nodes) {
                  const position = moved.get(item.id)
                  if (position) { item.x = position.x; item.y = position.y }
                }
              }), '')
              if (!changed) setFlowNodes((previous) => previous.map((item) => {
                const actual = useProcessStore.getState().current?.nodes.find((candidate) => candidate.id === item.id)
                return actual ? { ...item, position: { x: actual.x, y: actual.y }, dragging: false } : item
              }))
            }}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d9e1eb" />
            <Controls showInteractive={false} />
          </ReactFlow>
          {!nodes.length && !loading && (
            <div className="process-empty">
              <div className="process-empty__content">
                <div className="process-empty__mark" aria-hidden="true"><span /><i /><span /><i /><span /></div>
                <h2>从一个步骤开始</h2>
                <p>添加节点，再用连线串起业务如何运行。</p>
                <Button type="primary" disabled={readOnly} onClick={() => void addNode('start')}>添加开始节点</Button>
                <small>也可以从左侧拖入节点</small>
              </div>
            </div>
          )}
          {loading && <div className="process-loading"><Spin tip="正在加载流程"><div style={{ width: 160, height: 80 }} /></Spin></div>}
          <div className="process-canvas__hint">{selection ? '选中节点或连线后，在右侧编辑属性' : '左键框选 · 右键平移 · 滚轮缩放'}</div>
        </div>
        <ProcessProperties assetLoading={assetLoading} assetError={assetError} onRetryAssets={() => void refreshAssets()} onOpenAsset={onOpenAsset} onDelete={deleteItems} onSave={save} />
      </div>
    </div>
  )
}
