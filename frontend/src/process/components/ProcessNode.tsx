import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { LinkOutlined, UserOutlined } from '@ant-design/icons'
import type { ProcessNode as ProcessNodeRecord } from '../types'
import { PROCESS_NODE_META } from '../meta'

export interface ProcessNodeData extends Record<string, unknown> {
  node: ProcessNodeRecord
}

export type ProcessFlowNode = Node<ProcessNodeData, 'processNode'>

export function ProcessNode({ data, selected }: NodeProps<ProcessFlowNode>) {
  const node = data.node
  const meta = PROCESS_NODE_META[node.type]
  return (
    <div
      className={`process-node process-node--${node.type}${selected ? ' is-selected' : ''}`}
      style={{ width: meta.width, height: meta.height }}
      title={node.name}
    >
      {node.type === 'decision' && (
        <svg className="process-node__diamond" viewBox="0 0 180 104" aria-hidden="true">
          <polygon points="90,1 179,52 90,103 1,52"
            fill={selected ? '#f7faff' : '#fff'} stroke={selected ? '#2563eb' : '#8dabd2'}
            strokeWidth={selected ? 2.4 : 1.5} strokeLinejoin="round" />
        </svg>
      )}
      {node.type !== 'start' && <Handle id="in" type="target" position={Position.Left} aria-label="连接至此节点" />}
      <div className="process-node__content">
        <span className="process-node__name">{node.name}</span>
        {node.type === 'task' && (node.owner || node.assetIds.length > 0) && (
          <div className="process-node__context">
            {node.owner && <span title={`负责人：${node.owner}`}><UserOutlined /> {node.owner}</span>}
            {node.assetIds.length > 0 && <span><LinkOutlined /> {node.assetIds.length}</span>}
          </div>
        )}
      </div>
      {node.type === 'decision' ? (
        <>
          <span className="process-node__branch process-node__branch--yes">是</span>
          <Handle id="yes" type="source" position={Position.Right} aria-label="是分支" />
          <span className="process-node__branch process-node__branch--no">否</span>
          <Handle id="no" type="source" position={Position.Bottom} aria-label="否分支" />
        </>
      ) : node.type !== 'end' && (
        <Handle id="out" type="source" position={Position.Right} aria-label="从此节点连接" />
      )}
    </div>
  )
}
