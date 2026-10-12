/** 完整受控画布：拖动/选择先更新临时状态，结束后以一次原子编辑保存。 */
import {
  Background, Controls, MiniMap, ReactFlow, useReactFlow,
  SelectionMode,
  applyEdgeChanges,
  type Connection, type Edge as RfEdge, type NodeChange, type EdgeChange,
} from '@xyflow/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { App as AntdApp } from 'antd'
import { useStore } from '../store'
import type { GraphNode } from '../types'
import { DataNode, type DataFlowNodeType } from './DataNode'
import { EDGE_TYPE_LABEL } from './meta'
import { applyEditableNodeChanges, finalNodePositions } from '../utils/nodePositions'

const nodeTypes = { dataNode: DataNode }

interface MenuItem {
  key: 'up' | 'down' | 'clear' | 'delete'
  label: string
  danger?: boolean
}

export function FlowCanvas() {
  const { message, modal } = AntdApp.useApp()
  const { screenToFlowPosition } = useReactFlow()
  const nodes = useStore((s) => s.nodes)
  const edges = useStore((s) => s.edges)
  const trace = useStore((s) => s.trace)
  const select = useStore((s) => s.select)
  const canvasSelection = useStore((s) => s.canvasSelection)
  const busy = useStore((s) => s.busy)
  const [menu, setMenu] = useState<{ x: number; y: number; node: GraphNode } | null>(null)

  const displayNodes = useMemo(
    (): DataFlowNodeType[] => nodes.map((n) => ({
      id: n.id,
      type: 'dataNode',
      position: { x: n.x, y: n.y },
      data: {
        id: n.id, type: n.type, name: n.name, attrs: n.attrs,
        origin: n.origin, vanished: n.vanished, manualFields: n.manualFields,
        dimmed: !!trace && !trace.nodeIds.has(n.id),
        highlight: !!trace && trace.nodeIds.has(n.id) && trace.nodeId !== n.id,
      },
    })),
    [nodes, trace],
  )

  const displayEdges = useMemo(
    (): RfEdge[] => edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label || EDGE_TYPE_LABEL[e.type] || e.type,
      animated: e.type === 'lineage',
      style: {
        stroke: trace
          ? trace.edgeIds.has(e.id)
            ? '#faad14'
            : '#ddd'
          : e.type === 'lineage'
            ? '#fa8c16'
            : '#94a3b8',
        strokeWidth: trace && trace.edgeIds.has(e.id) ? 2.5 : 1.5,
      },
      labelStyle: { fontSize: 11, fill: '#555' },
      labelBgStyle: { fill: '#fff', fillOpacity: 0.85 },
    })),
    [edges, trace],
  )

  // 返回数据图时直接带入已加载节点及选择，避免空画布的选择回调清掉资产定位。
  const [rfNodes, setRfNodes] = useState<DataFlowNodeType[]>(() => displayNodes.map((node) => ({
    ...node, selected: canvasSelection.nodes.includes(node.id),
  })))
  const [rfEdges, setRfEdges] = useState<RfEdge[]>(() => displayEdges.map((edge) => ({
    ...edge, selected: canvasSelection.edges.includes(edge.id),
  })))

  useEffect(() => {
    setRfNodes((previous) => displayNodes.map((node) => {
      const old = previous.find((n) => n.id === node.id)
      return {
        ...old, ...node,
        position: old?.dragging ? old.position : node.position,
        selected: canvasSelection.nodes.includes(node.id),
      }
    }))
  }, [displayNodes, canvasSelection])

  useEffect(() => {
    setRfEdges(displayEdges.map((edge) => ({ ...edge, selected: canvasSelection.edges.includes(edge.id) })))
  }, [displayEdges, canvasSelection])

  const onNodesChange = useCallback((changes: NodeChange<DataFlowNodeType>[]) => {
    const state = useStore.getState()
    setRfNodes((current) => applyEditableNodeChanges(changes, current, state.busy))
    if (state.busy) return
    const positions = finalNodePositions(changes)
    if (!positions.size) return
    // 鼠标拖动、多选移动和方向键均由最终坐标批次触发一次原子保存。
    void state.saveLayout(Object.fromEntries(positions)).catch((error) => {
      message.error(`位置保存失败：${(error as Error).message}`)
      setRfNodes((previous) => previous.map((node) => {
        const actual = useStore.getState().nodes.find((item) => item.id === node.id)
        return actual && positions.has(node.id) ? { ...node, position: { x: actual.x, y: actual.y }, dragging: false } : node
      }))
    })
  }, [message])
  const onEdgesChange = useCallback((changes: EdgeChange<RfEdge>[]) => {
    setRfEdges((current) => applyEdgeChanges(changes, current))
  }, [])

  const onDrop = useCallback(
    async (event: React.DragEvent) => {
      event.preventDefault()
      const type = event.dataTransfer.getData('application/dataflow-type') as GraphNode['type']
      if (!type) return
      const name = `${TYPE_FALLBACK_NAME[type] ?? '新节点'} ${Date.now() % 10000}`
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
      try {
        await useStore.getState().createNode({ type, name, x: position.x, y: position.y })
        message.success(`已创建 ${name}`)
      } catch (e) {
        message.error(`创建失败：${(e as Error).message}`)
      }
    },
    [screenToFlowPosition, message],
  )

  const onConnect = useCallback(
    async (connection: Connection) => {
      if (!connection.source || !connection.target) return
      if (connection.source === connection.target) {
        message.warning('不能连接到自身')
        return
      }
      try {
        await useStore.getState().createEdge({ source: connection.source, target: connection.target, type: 'flow', label: '数据流' })
        message.success('已建立数据流')
      } catch (e) {
        message.error(`连线失败：${(e as Error).message}`)
      }
    },
    [message],
  )

  const deleteNode = useCallback(
    (node: GraphNode) => {
      modal.confirm({
        title: `删除节点「${node.name}」？`,
        content: '同时删除与它相连的所有数据流。',
        okType: 'danger',
        onOk: async () => {
          try {
            await useStore.getState().deleteItems([node.id])
          } catch (error) {
            message.error(`删除失败：${(error as Error).message}`)
            throw error
          }
        },
      })
    },
    [modal, message],
  )

  const traceFrom = useCallback(
    async (node: GraphNode, direction: 'upstream' | 'downstream') => {
      try {
        await useStore.getState().runTrace(node.id, direction)
        message.success(direction === 'upstream' ? '已高亮全部上游' : '已高亮全部下游')
      } catch (error) {
        message.error(`追溯失败：${(error as Error).message}`)
      }
    },
    [message],
  )

  // 节点右键菜单；画布空白处右键只屏蔽浏览器原生菜单
  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: { id: string }) => {
      event.preventDefault()
      const gn = nodes.find((n) => n.id === node.id)
      if (gn) setMenu({ x: event.clientX, y: event.clientY, node: gn })
    },
    [nodes],
  )

  // 菜单打开时，点击/滚动页面任意位置关闭
  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    window.addEventListener('click', close)
    window.addEventListener('wheel', close, { passive: true })
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('wheel', close)
    }
  }, [menu])

  return (
    <div style={{ width: '100%', height: '100%' }} onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodesDraggable={!busy}
        nodesConnectable={!busy}
        fitView
        minZoom={0.1}
        selectionOnDrag
        panOnDrag={[1, 2]}
        selectionMode={SelectionMode.Partial}
        deleteKeyCode={['Delete', 'Backspace']}
        onSelectionChange={({ nodes: selectedNodes, edges: selectedEdges }) => {
          // React Flow 初始化时先报告空选择；保留受控节点上尚待同步的选择。
          if (!selectedNodes.length && !selectedEdges.length
            && (rfNodes.some((node) => node.selected) || rfEdges.some((edge) => edge.selected))) return
          useStore.getState().setCanvasSelection(selectedNodes.map((n) => n.id), selectedEdges.map((e) => e.id))
        }}
        onNodeClick={(_, node) => select({ kind: 'node', id: node.id })}
        onEdgeClick={(_, edge) => select({ kind: 'edge', id: edge.id })}
        onPaneClick={() => select(null)}
        onConnect={onConnect}
        onBeforeDelete={async ({ nodes: deletedNodes, edges: deletedEdges }) => {
          if (busy) return false
          try {
            const changed = await useStore.getState().deleteItems(deletedNodes.map((n) => n.id), deletedEdges.map((e) => e.id))
            if (changed) message.success('已删除选中项')
          } catch (error) {
            message.error(`删除失败：${(error as Error).message}`)
          }
          // 只由成功保存后的完整图更新受控数据，避免节点/边分两次删除。
          return false
        }}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={(event) => event.preventDefault()}
      >
        <Background gap={18} />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
      {menu && (
        <div
          style={{
            position: 'fixed', left: menu.x, top: menu.y, zIndex: 1000,
            background: '#fff', borderRadius: 8, boxShadow: '0 3px 14px rgba(0,0,0,0.18)',
            padding: 4, minWidth: 150,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {([
            { key: 'up', label: '追溯上游来源' },
            { key: 'down', label: '追溯下游去向' },
            { key: 'clear', label: '清除追溯高亮' },
            { key: 'delete', label: '删除节点', danger: true },
          ] as MenuItem[]).map((item) => (
            <div
              key={item.key}
              style={{
                padding: '6px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
                color: item.danger ? '#ff4d4f' : '#333',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#f5f5f5')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => {
                const node = menu.node
                setMenu(null)
                if (item.key === 'up') void traceFrom(node, 'upstream')
                if (item.key === 'down') void traceFrom(node, 'downstream')
                if (item.key === 'clear') useStore.getState().clearTrace()
                if (item.key === 'delete') deleteNode(node)
              }}
            >
              {item.label}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const TYPE_FALLBACK_NAME: Record<string, string> = {
  system: '新系统',
  external: '新外部方',
  database: '新数据库',
  table: '新表',
  api: '新接口',
  file: '新文件',
  process_step: '新环节',
}
