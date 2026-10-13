import {
  Background, BackgroundVariant, Controls, MarkerType, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge as FlowEdge,
} from '@xyflow/react'
import { useEffect, useMemo } from 'react'
import { ProcessNode, type ProcessFlowNode } from '../components/ProcessNode'
import { PROCESS_NODE_META } from '../meta'
import type { ProcessCreate } from '../types'
import { previewEdgeLabel, startBridgeTargetIds } from './draftModel'
import '../process.css'

const nodeTypes = { processNode: ProcessNode }

interface Props {
  draft: ProcessCreate
  unreachableNodeIds: ReadonlySet<string>
  focusNodeId: string | null
  onFocusNode: (nodeId: string | null) => void
}

function DraftCanvas({ draft, unreachableNodeIds, focusNodeId, onFocusNode }: Props) {
  const { fitView } = useReactFlow<ProcessFlowNode, FlowEdge>()
  const bridged = useMemo(
    () => startBridgeTargetIds(draft.nodes, draft.edges, unreachableNodeIds),
    [draft.edges, draft.nodes, unreachableNodeIds],
  )
  const flowNodes = useMemo((): ProcessFlowNode[] => draft.nodes.map((node) => ({
    id: node.id,
    type: 'processNode',
    position: { x: node.x, y: node.y },
    width: PROCESS_NODE_META[node.type].width,
    height: PROCESS_NODE_META[node.type].height,
    data: { node },
    selected: node.id === focusNodeId,
    draggable: false,
    connectable: false,
    className: [
      unreachableNodeIds.has(node.id) ? 'ai-draft-node--unreachable' : '',
      node.id === focusNodeId ? 'ai-draft-node--focus' : '',
    ].filter(Boolean).join(' ') || undefined,
  })), [draft.nodes, focusNodeId, unreachableNodeIds])
  const flowEdges = useMemo((): FlowEdge[] => draft.edges.map((edge) => {
    const bridgedEdge = bridged.has(edge.target) && draft.nodes.some((node) => node.id === edge.source && node.type === 'start')
    return {
      ...edge,
      label: previewEdgeLabel(edge.label, bridgedEdge),
      sourceHandle: edge.sourceHandle ?? (draft.nodes.find((node) => node.id === edge.source)?.type === 'decision' ? 'yes' : 'out'),
      targetHandle: edge.targetHandle ?? 'in',
      type: 'smoothstep',
      className: bridgedEdge ? 'ai-draft-edge--bridge' : undefined,
      markerEnd: { type: MarkerType.ArrowClosed, color: bridgedEdge ? '#ea580c' : '#7c8da5', width: 18, height: 18 },
      style: bridgedEdge
        ? { stroke: '#ea580c', strokeWidth: 2.2, strokeDasharray: '6 4' }
        : { stroke: '#7c8da5', strokeWidth: 1.6 },
      labelStyle: bridgedEdge ? { fill: '#c2410c', fontSize: 12, fontWeight: 600 } : { fill: '#475569', fontSize: 12 },
      labelBgStyle: { fill: bridgedEdge ? '#fff7ed' : '#fff', fillOpacity: 0.96 },
      labelBgPadding: [7, 4],
      labelBgBorderRadius: 4,
    }
  }), [bridged, draft.edges, draft.nodes])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fitView(focusNodeId
        ? { nodes: [{ id: focusNodeId }], padding: 0.45, duration: 280, maxZoom: 1.15 }
        : { padding: 0.2, duration: 280, maxZoom: 1 })
    }, 40)
    return () => window.clearTimeout(timer)
  }, [draft, fitView, focusNodeId])

  return (
    <div className="ai-draft__canvas">
      <ReactFlow<ProcessFlowNode, FlowEdge>
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        edgesReconnectable={false}
        panOnDrag
        selectionOnDrag={false}
        deleteKeyCode={null}
        multiSelectionKeyCode={null}
        minZoom={0.08}
        maxZoom={2}
        onNodeClick={(_, node) => onFocusNode(node.id)}
        onPaneClick={() => onFocusNode(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d9e1eb" />
        <Controls showInteractive={false} />
      </ReactFlow>
      {unreachableNodeIds.size > 0 && (
        <p className="ai-draft__legend">橙色标出的步骤接不上主流程。从「开始」拉出的虚线只是把它留在图里，不是并列分支。</p>
      )}
      <div className="ai-draft__hint">只读预览 · 右键平移 · 滚轮缩放</div>
    </div>
  )
}

export function DraftPreview(props: Props) {
  return (
    <ReactFlowProvider>
      <DraftCanvas {...props} />
    </ReactFlowProvider>
  )
}
