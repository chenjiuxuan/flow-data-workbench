/** 节点类型的中文名、图标与配色。 */
import {
  ApartmentOutlined, ApiOutlined, CloudServerOutlined, DatabaseOutlined,
  FileTextOutlined, FundOutlined, ShopOutlined, TableOutlined, TeamOutlined,
} from '@ant-design/icons'
import type { NodeType } from '../types'

export const NODE_TYPE_META: Record<NodeType, { label: string; color: string; icon: typeof ShopOutlined }> = {
  system: { label: '系统', color: '#1677ff', icon: ShopOutlined },
  external: { label: '外部方', color: '#722ed1', icon: TeamOutlined },
  database: { label: '数据库', color: '#13c2c2', icon: DatabaseOutlined },
  table: { label: '表', color: '#fa8c16', icon: TableOutlined },
  api: { label: '接口', color: '#eb2f96', icon: ApiOutlined },
  file: { label: '文件', color: '#52c41a', icon: FileTextOutlined },
  process_step: { label: '业务环节', color: '#2f54eb', icon: FundOutlined },
}

export const TYPE_PALETTE: { type: NodeType; icon: typeof ApartmentOutlined }[] = [
  { type: 'system', icon: ShopOutlined },
  { type: 'external', icon: TeamOutlined },
  { type: 'database', icon: CloudServerOutlined },
  { type: 'table', icon: TableOutlined },
  { type: 'api', icon: ApiOutlined },
  { type: 'file', icon: FileTextOutlined },
  { type: 'process_step', icon: ApartmentOutlined },
]

export const VIEW_TITLE: Record<string, string> = {
  system: '系统视图：系统 / 数据库 / 接口 / 文件 / 外部方之间的数据流转',
  business: '业务视图：业务环节及其承载系统',
  field: '字段视图：表级血缘（连线含字段映射，点选连线查看）',
}

export const EDGE_TYPE_LABEL: Record<string, string> = {
  flow: '数据流',
  lineage: '血缘',
  uses: '使用',
}
