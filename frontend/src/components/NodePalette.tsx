/** 节点库：拖拽入口、快捷键和可折叠的画布操作说明。 */
import { HolderOutlined, QuestionCircleOutlined, RightOutlined } from '@ant-design/icons'
import { useState, type CSSProperties } from 'react'
import { useStore } from '../store'
import type { NodeType } from '../types'
import { NODE_TYPE_META, TYPE_PALETTE } from './meta'
import './NodePalette.css'

const VIEW_HINT: Partial<Record<NodeType, string>> = {
  table: '字段视图',
  process_step: '业务视图',
}

const SHORTCUTS = [
  { label: '撤销', key: 'Ctrl Z' },
  { label: '重做', key: 'Ctrl Y' },
  { label: '删除选中', key: 'Delete' },
  { label: '缩放画布', key: '滚轮' },
  { label: '平移画布', key: '右键拖拽' },
]

export function NodePalette() {
  const busy = useStore((s) => s.busy)
  const [dragging, setDragging] = useState<NodeType | null>(null)

  return (
    <div className="node-palette">
      <header className="node-palette__header">
        <h2>节点库</h2>
        <p id="node-palette-hint">拖到画布，开始连接</p>
      </header>

      <ul className="node-palette__list" aria-label="可拖拽节点" aria-describedby="node-palette-hint">
        {TYPE_PALETTE.map(({ type, icon: Icon }) => {
          const meta = NODE_TYPE_META[type]
          const hint = VIEW_HINT[type]
          return (
            <li
              key={type}
              className={`node-palette__item${dragging === type ? ' is-dragging' : ''}${busy ? ' is-disabled' : ''}`}
              style={{ '--node-color': meta.color, '--node-tint': `${meta.color}0d` } as CSSProperties}
              draggable={!busy}
              aria-disabled={busy || undefined}
              title={busy ? '正在保存，请稍候' : `拖拽创建「${meta.label}」节点${hint ? `，在${hint}中查看` : ''}`}
              onDragStart={(event) => {
                if (useStore.getState().busy) {
                  event.preventDefault()
                  return
                }
                event.dataTransfer.setData('application/dataflow-type', type)
                event.dataTransfer.effectAllowed = 'move'
                setDragging(type)
              }}
              onDragEnd={() => setDragging(null)}
            >
              <span className="node-palette__icon" aria-hidden="true"><Icon /></span>
              <span className="node-palette__name">{meta.label}</span>
              {hint && <span className="node-palette__view">{hint}</span>}
              <HolderOutlined className="node-palette__grip" aria-hidden="true" />
            </li>
          )
        })}
      </ul>

      <footer className="node-palette__footer">
        <section className="node-palette__shortcuts" aria-labelledby="node-palette-shortcuts">
          <h3 id="node-palette-shortcuts">快捷操作</h3>
          <dl>
            {SHORTCUTS.map(({ label, key }) => (
              <div className="node-palette__shortcut" key={label}>
                <dt>{label}</dt>
                <dd><kbd>{key}</kbd></dd>
              </div>
            ))}
          </dl>
        </section>

        <details className="node-palette__guide">
          <summary>
            <QuestionCircleOutlined aria-hidden="true" />
            <span>画布操作说明</span>
            <RightOutlined className="node-palette__chevron" aria-hidden="true" />
          </summary>
          <ul>
            <li>从节点右侧圆点拖到另一节点，建立数据流。</li>
            <li>在画布空白处按住左键拖拽，可框选多个节点。</li>
            <li>右键点击节点，可追溯上游来源和下游去向。</li>
          </ul>
        </details>
      </footer>
    </div>
  )
}
