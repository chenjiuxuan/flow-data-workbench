/** 不展开节点数组，避免大图超过函数参数数量限制。 */
export function nodeBounds(nodes: readonly { x: number; y: number }[]) {
  if (!nodes.length) return null
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const node of nodes) {
    minX = Math.min(minX, node.x)
    minY = Math.min(minY, node.y)
    maxX = Math.max(maxX, node.x)
    maxY = Math.max(maxY, node.y)
  }
  return { minX, minY, maxX, maxY }
}
