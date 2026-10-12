/** 画布节点卡片：简洁美化版。 */
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { Tag } from 'antd'
import { NODE_TYPE_META } from './meta'
import type { GraphNode } from '../types'

export interface DataNodeData extends Record<string, unknown> {
  id: string
  type: GraphNode['type']
  name: string
  attrs: Record<string, unknown>
  origin: 'auto' | 'manual'
  vanished: boolean
  manualFields: string[]
  dimmed: boolean
  highlight: boolean
}

export type DataFlowNodeType = Node<DataNodeData, 'dataNode'>

export function DataNode({ data, selected }: NodeProps<DataFlowNodeType>) {
  const meta = NODE_TYPE_META[data.type] ?? NODE_TYPE_META.system
  const borderColor = data.highlight ? '#faad14' : selected ? '#1677ff' : meta.color
  return (
    <div
      title={data.name}
      style={{
        minWidth: 140,
        maxWidth: 220,
        padding: '10px 14px',
        borderRadius: 10,
        background: '#fff',
        border: `2px solid ${borderColor}`,
        boxShadow: data.highlight
          ? '0 0 0 3px rgba(250, 173, 20, 0.3), 0 4px 12px rgba(0,0,0,0.1)'
          : selected
            ? '0 0 0 2px rgba(22, 119, 255, 0.2), 0 2px 8px rgba(0,0,0,0.08)'
            : '0 1px 4px rgba(0,0,0,0.06)',
        opacity: data.dimmed ? 0.25 : 1,
        transition: 'box-shadow 0.2s, opacity 0.2s',
        cursor: 'grab',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: meta.color, width: 10, height: 10 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <meta.icon style={{ color: meta.color, fontSize: 16 }} />
        <span style={{ fontWeight: 600, fontSize: 13, color: '#262626', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {data.name}
        </span>
      </div>
      <div style={{ marginTop: 6, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        <Tag color={meta.color} style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px', borderRadius: 4 }}>
          {meta.label}
        </Tag>
        {data.origin === 'auto' && (
          <Tag style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px', borderRadius: 4, color: '#8c8c8c' }}>
            自动
          </Tag>
        )}
        {data.vanished && (
          <Tag color="red" style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px', borderRadius: 4 }}>
            源中已消失
          </Tag>
        )}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: meta.color, width: 10, height: 10 }} />
    </div>
  )
}
