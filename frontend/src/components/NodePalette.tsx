/** 左侧面板：操作引导 + 节点类型拖拽。 */
import { Card, Tag } from 'antd'
import { NODE_TYPE_META, TYPE_PALETTE } from './meta'

const TIPS = [
  { icon: '🖱️', text: '拖拽节点到画布创建' },
  { icon: '🔗', text: '从节点右侧圆点拖到另一节点建数据流' },
  { icon: '⬜', text: '空白处左键拖拽框选，右键拖拽平移' },
  { icon: '⌨️', text: 'Delete 删除选中 · Ctrl+Z 撤销' },
  { icon: '', text: '右键节点可追溯上下游' },
]

export function NodePalette() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 10 }}>
      {/* 操作引导卡片 */}
      <Card
        size="small"
        title={<span style={{ fontSize: 13 }}>🚀 快速上手</span>}
        styles={{ body: { padding: '8px 10px' } }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {TIPS.map((tip, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <span style={{ fontSize: 14 }}>{tip.icon}</span>
              <span>{tip.text}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* 节点类型面板 */}
      <Card
        size="small"
        title={<span style={{ fontSize: 13 }}>📦 节点类型（拖到画布）</span>}
        styles={{ body: { padding: '8px', display: 'flex', flexDirection: 'column', gap: 6 } }}
      >
        {TYPE_PALETTE.map(({ type, icon: Icon }) => {
          const meta = NODE_TYPE_META[type]
          return (
            <div
              key={type}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData('application/dataflow-type', type)
                e.dataTransfer.effectAllowed = 'move'
              }}
              title={`拖拽创建「${meta.label}」节点`}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px',
                border: `1px solid ${meta.color}44`, borderRadius: 8, cursor: 'grab',
                background: `${meta.color}0d`, userSelect: 'none',
                transition: 'all 0.2s',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = `${meta.color}22`)}
              onMouseLeave={(e) => (e.currentTarget.style.background = `${meta.color}0d`)}
            >
              <Icon style={{ color: meta.color, fontSize: 16 }} />
              <span style={{ fontSize: 13, fontWeight: 500 }}>{meta.label}</span>
              {type === 'process_step' && <Tag style={{ marginLeft: 'auto', fontSize: 10, lineHeight: '14px' }}>业务</Tag>}
              {type === 'external' && <Tag style={{ marginLeft: 'auto', fontSize: 10, lineHeight: '14px' }}>外部</Tag>}
            </div>
          )
        })}
      </Card>

      {/* 快捷键参考 */}
      <Card
        size="small"
        title={<span style={{ fontSize: 13 }}>⌨️ 快捷键</span>}
        styles={{ body: { padding: '8px 10px' } }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, color: '#666' }}>
          <div><Tag color="blue" style={{ fontSize: 10 }}>Ctrl+Z</Tag> 撤销</div>
          <div><Tag color="blue" style={{ fontSize: 10 }}>Ctrl+Y</Tag> 重做</div>
          <div><Tag color="blue" style={{ fontSize: 10 }}>Delete</Tag> 删除选中</div>
          <div><Tag color="blue" style={{ fontSize: 10 }}>滚轮</Tag> 缩放</div>
          <div><Tag color="blue" style={{ fontSize: 10 }}>右键拖拽</Tag> 平移画布</div>
        </div>
      </Card>
    </div>
  )
}
