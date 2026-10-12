import { applyNodeChanges, type Node, type NodeChange, type XYPosition } from '@xyflow/react'

/** React Flow 的鼠标拖动结束与方向键移动都通过同一批最终 position change 提交。 */
export function finalNodePositions<T extends Node>(changes: NodeChange<T>[]): Map<string, XYPosition> {
  const positions = new Map<string, XYPosition>()
  for (const change of changes) {
    if (change.type === 'position' && change.dragging === false && change.position) {
      positions.set(change.id, { ...change.position })
    }
  }
  return positions
}

export function applyEditableNodeChanges<T extends Node>(changes: NodeChange<T>[], nodes: T[], readOnly: boolean): T[] {
  if (!readOnly) return applyNodeChanges(changes, nodes)
  const rejected = new Set(changes.filter((change) => change.type === 'position').map((change) => change.id))
  // React Flow 已先修改内部坐标，拒绝时仍需发布新对象使受控值重新生效。
  return applyNodeChanges(changes.filter((change) => change.type !== 'position'), nodes).map((node) => rejected.has(node.id)
    ? { ...node, position: { ...node.position }, dragging: false }
    : node)
}
