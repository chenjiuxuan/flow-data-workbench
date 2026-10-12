/** 模板只生成流程本身，不创建或虚构组织中的系统与数据资产。 */
import type { ProcessCreate, ProcessEdge, ProcessNode, ProcessNodeType } from './types'

export function emptyProcess(name: string, description = ''): ProcessCreate {
  return { name, description, nodes: [], edges: [] }
}

export function approvalProcess(name = '申请审批流程', description = ''): ProcessCreate {
  const node = (id: string, type: ProcessNodeType, name: string, x: number, y: number): ProcessNode => ({
    id, type, name, x, y, owner: '', description: '', input: '', output: '', assetIds: [],
  })
  const edge = (source: string, target: string, label = '', sourceHandle = 'out'): ProcessEdge => ({
    id: `${source}-${target}`, source, target, label, sourceHandle, targetHandle: 'in',
  })
  return {
    name, description,
    nodes: [
      // 主链中心线一致，配合左右句柄；退回环路放在下方，首次打开即可阅读。
      node('start', 'start', '开始', 36, 150),
      node('submit', 'task', '提交申请', 224, 138),
      node('review', 'task', '审核申请', 444, 138),
      node('decision', 'decision', '是否通过？', 664, 124),
      node('execute', 'task', '执行', 904, 138),
      node('end', 'end', '结束', 1144, 150),
      node('revise', 'task', '修改申请', 444, 350),
    ],
    edges: [
      edge('start', 'submit'), edge('submit', 'review'), edge('review', 'decision'),
      edge('decision', 'execute', '通过', 'yes'), edge('execute', 'end'),
      edge('decision', 'revise', '不通过', 'no'), edge('revise', 'review'),
    ],
  }
}
