import { BorderOutlined, BranchesOutlined, CheckCircleOutlined, PlayCircleOutlined } from '@ant-design/icons'
import type { ProcessNodeType } from './types'

export const PROCESS_NODE_TYPES: ProcessNodeType[] = ['start', 'task', 'decision', 'end']

export const PROCESS_NODE_META = {
  start: { label: '开始', hint: '流程的起点', icon: PlayCircleOutlined, width: 128, height: 52 },
  task: { label: '处理步骤', hint: '需要完成的工作', icon: BorderOutlined, width: 180, height: 76 },
  decision: { label: '条件判断', hint: '按条件进入不同分支', icon: BranchesOutlined, width: 180, height: 104 },
  end: { label: '结束', hint: '流程的终点', icon: CheckCircleOutlined, width: 128, height: 52 },
} as const
