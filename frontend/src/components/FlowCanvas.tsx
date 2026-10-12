/** 主画布：渲染图、拖拽建点、连线建边、选择、追溯高亮、右键菜单。

非受控模式：节点/边直接作为 ReactFlow 初始值传入，不接 onNodesChange/onEdgesChange，
避免受控模式下 useEffect setState 与内部 store 的时序竞态。
节点位置由 onNodeDragStop 单点保存；选中/删除由 getNodes/getEdges 读取。
*/
import {
  Background, Controls, MiniMap, ReactFlow, useReactFlow,
  SelectionMode,
  type Connection, type Edge as RfEdge,
} from '@xyflow/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { App as AntdApp } from 'antd'
import { api } from '../api/client'
import { useStore } from '../store'
import type { GraphNode } from '../types'
import { DataNode, type DataFlowNodeType } from './DataNode'
import { EDGE_TYPE_LABEL } from './meta'

const nodeTypes = { dataNode: DataNode }

interface MenuItem {
  key: 'up' | 'down' | 'clear' | 'delete'
  label: string
  danger?: boolean
}

export function FlowCanvas() {
  const { message, modal } = AntdApp.useApp()
  const { screenToFlowPosition, getNodes } = useReactFlow()
  const nodes = useStore((s) => s.nodes)
  const edges = useStore((s) => s.edges)
  const trace = useStore((s) => s.trace)
  const select = useStore((s) => s.select)
  const refresh = useStore((s) => s.refresh)
  const [menu, setMenu] = useState<{ x: number; y: number; node: GraphNode } | null>(null)

  // 派生初始值：store 变化时直接替换整个 ReactFlow 子树（key 重建），
  // 避免 onChange 处理器在受控模式里的双向同步问题。
  // store 变化时派生值变化，ReactFlow 作为非受控组件，
  // 新 props 会被内部 store 在下一帧同步（useNodesInternal）。
  void useMemo(
    () => `${nodes.length}|${edges.length}|${trace?.nodeIds.size ?? 0}|${nodes.map((n) => `${n.id}:${n.x.toFixed(1)}:${n.y.toFixed(1)}`).join(',')}`,
    [nodes, edges, trace],
  )

  const rfNodes = useMemo(
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

  const rfEdges = useMemo(
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

  const onDrop = useCallback(
    async (event: React.DragEvent) => {
      event.preventDefault()
      const type = event.dataTransfer.getData('application/dataflow-type') as GraphNode['type']
      if (!type) return
      const name = `${TYPE_FALLBACK_NAME[type] ?? '新节点'} ${Date.now() % 10000}`
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
      try {
        await api.createNode({ type, name, x: position.x, y: position.y })
        await refresh()
        message.success(`已创建 ${name}`)
      } catch (e) {
        message.error(`创建失败：${(e as Error).message}`)
      }
    },
    [screenToFlowPosition, refresh, message],
  )

  const onConnect = useCallback(
    async (connection: Connection) => {
      if (!connection.source || !connection.target) return
      if (connection.source === connection.target) {
        message.warning('不能连接到自身')
        return
      }
      try {
        await api.createEdge({ source_id: connection.source, target_id: connection.target, type: 'flow', label: '数据流' })
        await refresh()
        message.success('已建立数据流')
      } catch (e) {
        message.error(`连线失败：${(e as Error).message}`)
      }
    },
    [refresh, message],
  )

  // 节点拖动结束：读取内部 store 当前选中节点，整组保存位置到后端
  const onNodeDragStop = useCallback(
    () => {
      const selected = getNodes().filter((n) => n.selected)
      if (selected.length === 0) return
      useStore.setState((s) => ({
        nodes: s.nodes.map((n) => {
          const t = selected.find((x) => x.id === n.id)
          return t ? { ...n, x: t.position.x, y: t.position.y } : n
        }),
      }))
      void api.saveLayout(
        Object.fromEntries(selected.map((t) => [t.id, [t.position.x, t.position.y] as [number, number]])),
      )
    },
    [getNodes],
  )

  // Delete 键删除选中（React Flow 已移除内部状态，这里同步后端）
  const onNodesDelete = useCallback(
    async (deleted: DataFlowNodeType[]) => {
      for (const n of deleted) await api.deleteNode(n.id)
      select(null)
      await refresh()
      message.success(`已删除 ${deleted.length} 个节点`)
    },
    [select, refresh, message],
  )

  const onEdgesDelete = useCallback(
    async (deleted: RfEdge[]) => {
      for (const e of deleted) await api.deleteEdge(e.id)
      select(null)
      await refresh()
      message.success(`已删除 ${deleted.length} 条连线`)
    },
    [select, refresh, message],
  )

  const deleteNode = useCallback(
    (node: GraphNode) => {
      modal.confirm({
        title: `删除节点「${node.name}」？`,
        content: '同时删除与它相连的所有数据流。',
        okType: 'danger',
        onOk: async () => {
          await api.deleteNode(node.id)
          select(null)
          await refresh()
        },
      })
    },
    [modal, select, refresh],
  )

  const traceFrom = useCallback(
    async (node: GraphNode, direction: 'upstream' | 'downstream') => {
      await useStore.getState().runTrace(node.id, direction)
      message.success(direction === 'upstream' ? '已高亮全部上游' : '已高亮全部下游')
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
        fitView
        minZoom={0.1}
        selectionOnDrag
        panOnDrag={[1, 2]}
        selectionMode={SelectionMode.Partial}
        deleteKeyCode={['Delete', 'Backspace']}
        onNodeClick={(_, node) => select({ kind: 'node', id: node.id })}
        onPaneClick={() => select(null)}
        onEdgeClick={(_, edge) => select({ kind: 'edge', id: edge.id })}
        onConnect={onConnect}
        onNodeDragStop={onNodeDragStop}
        onNodesDelete={onNodesDelete}
        onEdgesDelete={onEdgesDelete}
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
