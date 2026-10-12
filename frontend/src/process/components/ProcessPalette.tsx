import { DownOutlined, HolderOutlined, QuestionCircleOutlined } from '@ant-design/icons'
import { useState } from 'react'
import { PROCESS_NODE_META, PROCESS_NODE_TYPES } from '../meta'
import type { ProcessNodeType } from '../types'

export function ProcessPalette({ busy, onAdd }: { busy: boolean; onAdd: (type: ProcessNodeType) => void }) {
  const [dragging, setDragging] = useState<ProcessNodeType | null>(null)
  return (
    <aside className="process-palette" aria-label="流程节点库">
      <div className="process-palette__header">
        <h2>流程节点</h2>
        <p>拖到画布，或点击添加</p>
      </div>
      <div className="process-palette__list">
        {PROCESS_NODE_TYPES.map((type) => {
          const meta = PROCESS_NODE_META[type]
          return (
            <button
              key={type}
              type="button"
              className={`process-palette__item${dragging === type ? ' is-dragging' : ''}`}
              disabled={busy}
              draggable={!busy}
              onClick={() => onAdd(type)}
              onDragStart={(event) => {
                event.dataTransfer.setData('application/process-node-type', type)
                event.dataTransfer.effectAllowed = 'move'
                setDragging(type)
              }}
              onDragEnd={() => setDragging(null)}
              title={`添加${meta.label}节点，也可拖拽至画布`}
            >
              <span className={`process-palette__symbol process-palette__symbol--${type}`} aria-hidden="true"><meta.icon /></span>
              <span className="process-palette__label"><strong>{meta.label}</strong><small>{meta.hint}</small></span>
              <HolderOutlined className="process-palette__grip" aria-hidden="true" />
            </button>
          )
        })}
      </div>
      <div className="process-palette__footer">
        <div className="process-palette__keys">
          <h3>快捷操作</h3>
          <dl>
            <div><dt>撤销 / 重做</dt><dd><kbd>Ctrl Z / Y</kbd></dd></div>
            <div><dt>删除选中</dt><dd><kbd>Delete</kbd></dd></div>
            <div><dt>多选节点</dt><dd><kbd>Shift + 点击</kbd></dd></div>
          </dl>
        </div>
        <details className="process-palette__guide">
          <summary><QuestionCircleOutlined /> 如何连接流程<DownOutlined className="process-palette__chevron" /></summary>
          <p>从节点的右侧圆点拖向另一节点的左侧圆点，创建有向连线。</p>
          <p>判断节点提供“是”和“否”两个出口。选中连线可修改条件文字。</p>
          <p>左键拖拽空白处框选，右键拖动画布；滚轮缩放。</p>
        </details>
      </div>
    </aside>
  )
}
