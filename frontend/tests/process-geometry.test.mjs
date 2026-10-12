import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom', logLevel: 'error' })
const { finalNodePositions, applyEditableNodeChanges } = await vite.ssrLoadModule('/src/utils/nodePositions.ts')
const { layoutProcess } = await vite.ssrLoadModule('/src/process/layout.ts')
const { autoLayout } = await vite.ssrLoadModule('/src/utils/layout.ts')
after(async () => { await vite.close() })

test('方向键移动和多节点拖动结束都形成一批最终坐标，拖动中的临时值不提交', () => {
  const drag = { id: 'A', type: 'position', position: { x: 10, y: 20 }, dragging: true }
  assert.equal(finalNodePositions([drag]).size, 0)
  const keyboard = { ...drag, position: { x: 15, y: 20 }, dragging: false }
  assert.deepEqual([...finalNodePositions([keyboard])], [['A', { x: 15, y: 20 }]])
  assert.deepEqual([...finalNodePositions([drag, keyboard, { ...keyboard, id: 'B', position: { x: 50, y: 60 } }])], [
    ['A', { x: 15, y: 20 }], ['B', { x: 50, y: 60 }],
  ])
})

test('保存期间拒绝残留移动并重新同步受控节点，选择和测量仍可更新', () => {
  const nodes = [{ id: 'A', position: { x: 10, y: 20 }, data: {}, dragging: true }]
  const changes = [
    { id: 'A', type: 'position', position: { x: 500, y: 600 }, dragging: false },
    { id: 'A', type: 'select', selected: true },
    { id: 'A', type: 'dimensions', dimensions: { width: 180, height: 76 } },
  ]
  const locked = applyEditableNodeChanges(changes, nodes, true)
  assert.deepEqual(locked[0].position, nodes[0].position)
  assert.notEqual(locked[0], nodes[0])
  assert.equal(locked[0].dragging, false)
  assert.equal(locked[0].selected, true)
  assert.deepEqual(locked[0].measured, { width: 180, height: 76 })
  assert.deepEqual(applyEditableNodeChanges(changes, nodes, false)[0].position, { x: 500, y: 600 })
})

test('流程和数据图的对象保留键 ID 均可完成实际 dagre 布局，原始 ID 不丢失', () => {
  const node = (id, type) => ({ id, type, name: id, x: 0, y: 0, owner: '', description: '', input: '', output: '', assetIds: [] })
  const nodes = [node('__proto__', 'start'), node('constructor', 'task'), node('prototype', 'task'), node('toString', 'decision'), node('end', 'end')]
  const edges = [
    { id: 'A', source: '__proto__', target: 'constructor' },
    { id: 'B', source: 'constructor', target: 'prototype' },
    { id: 'C', source: 'prototype', target: 'toString' },
    { id: 'D', source: 'toString', target: 'end', sourceHandle: 'yes' },
  ]
  const positions = layoutProcess(nodes, edges)
  assert.deepEqual([...positions.keys()], nodes.map((item) => item.id))
  for (const position of positions.values()) assert.ok(Number.isFinite(position.x) && Number.isFinite(position.y))
  const dataPositions = autoLayout({ nodeIds: nodes.map((item) => item.id), edges })
  assert.deepEqual(Object.keys(dataPositions), nodes.map((item) => item.id))
  for (const position of Object.values(dataPositions)) assert.ok(Number.isFinite(position.x) && Number.isFinite(position.y))
  for (const edge of edges) {
    assert.ok(positions.get(edge.source).x < positions.get(edge.target).x)
    assert.ok(dataPositions[edge.source].x < dataPositions[edge.target].x)
  }
  assert.deepEqual(nodes.map((item) => [item.x, item.y]), nodes.map(() => [0, 0]))
})
