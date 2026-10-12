/** 导入与采集对话框：Excel 台账 / SQL 血缘 / 数据库采集 三个入口。 */
import { DownloadOutlined, DatabaseOutlined, FileExcelOutlined, ThunderboltOutlined } from '@ant-design/icons'
import {
  Alert, App as AntdApp, Button, Form, Input, Modal, Select, Space, Table, Tabs, Typography, Upload,
} from 'antd'
import { useState } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'
import type { MergeReport } from '../types'

const { Text } = Typography
const { TextArea } = Input

function ReportView({ report, extra }: { report: MergeReport | null; extra?: React.ReactNode }) {
  if (!report) return null
  const rows = [
    { item: '新增节点', count: report.added_nodes.length },
    { item: '更新节点', count: report.updated_nodes.length },
    { item: '新增数据流/血缘', count: report.added_edges.length },
    { item: '更新数据流/血缘', count: report.updated_edges.length },
    { item: '标记「源中已消失」节点', count: report.vanished_nodes.length },
    { item: '保护的人工修改', count: report.protected_fields.length },
  ]
  return (
    <div style={{ marginTop: 12 }}>
      <Alert
        type="success"
        message="并入完成"
        description={
          <Table
            size="small"
            rowKey="item"
            pagination={false}
            dataSource={rows}
            columns={[
              { title: '项目', dataIndex: 'item' },
              { title: '数量', dataIndex: 'count', width: 100 },
            ]}
          />
        }
      />
      {extra}
    </div>
  )
}

/** 导入结果如果落在别的视图（如 SQL 血缘进字段视图），自动切过去让用户立刻看到。 */
async function switchViewForImports(addedIds: string[] | undefined) {
  if (!addedIds?.length) return
  const store = useStore.getState()
  const hasTable = addedIds.some((id) => id.startsWith('tbl:'))
  const hasSystem = addedIds.some((id) => id.startsWith('sys:') || id.startsWith('db:'))
  if (hasTable && store.view !== 'field') await store.setView('field')
  else if (hasSystem && store.view === 'field') await store.setView('system')
}

function ExcelTab({ onDone }: { onDone: () => void }) {
  const { message } = AntdApp.useApp()
  const [result, setResult] = useState<React.ReactNode>(null)
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Text type="secondary">
        适合把公司里已有的数据流台账（Excel）一键成图。先下载模板填写，再上传。
      </Text>
      <Space>
        <Button icon={<DownloadOutlined />} href="/api/import/excel/template">
          下载模板
        </Button>
        <Upload
          accept=".xlsx"
          showUploadList={false}
          customRequest={async ({ file }) => {
            try {
              const r = await api.importExcel(file as File, true)
              setResult(<ReportView report={r.report} extra={r.errors.length > 0 && (
                <Alert style={{ marginTop: 8 }} type="warning" showIcon
                  message={`${r.errors.length} 行有问题`}
                  description={r.errors.slice(0, 5).map((e: string, i: number) => <div key={i}>{e}</div>)} />
              )} />)
              await switchViewForImports(r.report?.added_nodes)
              onDone()
              message.success('Excel 已导入')
            } catch (e) {
              message.error(`导入失败：${(e as Error).message}`)
            }
          }}
        >
          <Button type="primary" icon={<FileExcelOutlined />}>上传 Excel 并入图</Button>
        </Upload>
      </Space>
      {result}
    </Space>
  )
}

function SqlTab({ onDone }: { onDone: () => void }) {
  const { message } = AntdApp.useApp()
  const [form] = Form.useForm()
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.importSql>> | null>(null)

  const run = async (apply: boolean) => {
    const values = await form.validateFields()
    try {
      const r = await api.importSql({ ...values, apply })
      setResult(r)
      if (apply) {
        await switchViewForImports(r.report?.added_nodes)
        onDone()
        message.success('SQL 血缘已并入图（可在字段视图查看）')
      } else {
        message.info(`解析出 ${Object.keys(r.tables).length} 张表、${r.edges.length} 条血缘（未入图）`)
      }
    } catch (e) {
      message.error(`解析失败：${(e as Error).message}`)
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Text type="secondary">
        粘贴建表/同步/加工作业 SQL（INSERT-SELECT、CREATE TABLE AS、UPDATE、MERGE），自动解析表级+字段级血缘。
      </Text>
      <Form form={form} layout="vertical" initialValues={{ dialect: 'mysql', source_name: 'SQL 脚本' }} size="small">
        <Space>
          <Form.Item label="方言" name="dialect" style={{ minWidth: 140 }}>
            <Select options={['mysql', 'postgresql', 'oracle', 'hive', 'spark', 'duckdb', 'sqlite', 'tsql'].map((d) => ({ value: d, label: d }))} />
          </Form.Item>
          <Form.Item label="来源名称" name="source_name" style={{ minWidth: 220 }}>
            <Input placeholder="如：数仓日调度作业" />
          </Form.Item>
        </Space>
        <Form.Item label="SQL 内容" name="sql_text" rules={[{ required: true, message: '请粘贴 SQL' }]}>
          <TextArea rows={10} style={{ fontFamily: 'monospace' }} placeholder={'INSERT INTO dw.dwd_order\nSELECT ... FROM ods.orders'} />
        </Form.Item>
        <Space>
          <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => void run(true)}>解析并入图</Button>
          <Button onClick={() => void run(false)}>仅预览</Button>
        </Space>
      </Form>
      {result && (
        <>
          {result.errors.length > 0 && (
            <Alert type="warning" showIcon message={`${result.errors.length} 条语句解析失败`}
              description={result.errors.slice(0, 3).map((e, i) => <div key={i}>{e}</div>)} />
          )}
          <ReportView report={result.report} />
          {result.edges.length > 0 && (
            <Table
              size="small"
              rowKey={(r) => `${r.source}->${r.target}`}
              pagination={{ pageSize: 5 }}
              dataSource={result.edges}
              columns={[
                { title: '来源表', dataIndex: 'sourceName' },
                { title: '目标表', dataIndex: 'targetName' },
                { title: '字段映射', dataIndex: 'fields', render: (fields: string[]) =>
                  fields.length ? <Text style={{ fontSize: 12 }}>{fields.join('；')}</Text> : <Text type="secondary">-</Text> },
              ]}
            />
          )}
        </>
      )}
    </Space>
  )
}

function DbTab({ onDone }: { onDone: () => void }) {
  const { message } = AntdApp.useApp()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.discoverDb>> | null>(null)

  const run = async () => {
    const values = await form.validateFields()
    setLoading(true)
    try {
      const r = await api.discoverDb({ ...values, apply: true })
      setResult(r)
      onDone()
      message.success(`已采集 ${r.collected.tables} 张表`)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon icon={<DatabaseOutlined />}
        message="只读采集" description="仅读取库表结构（information_schema 级别），不会改动数据库。" />
      <Form form={form} layout="vertical" initialValues={{ db_type: 'mysql', port: 3306 }} size="small">
        <Space wrap>
          <Form.Item label="类型" name="db_type" style={{ minWidth: 120 }}>
            <Select options={[{ value: 'mysql', label: 'MySQL' }, { value: 'postgresql', label: 'PostgreSQL' }]} />
          </Form.Item>
          <Form.Item label="主机" name="host" rules={[{ required: true }]}><Input placeholder="127.0.0.1" /></Form.Item>
          <Form.Item label="端口" name="port" rules={[{ required: true }]}><Input type="number" /></Form.Item>
          <Form.Item label="用户" name="user" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="密码" name="password"><Input.Password /></Form.Item>
          <Form.Item label="库名" name="database" rules={[{ required: true }]}><Input /></Form.Item>
        </Space>
        <Button type="primary" loading={loading} onClick={() => void run()}>采集并入图</Button>
      </Form>
      {result && (
        <>
          {result.warnings.length > 0 && (
            <Alert type="warning" showIcon message="部分内容被截断"
              description={result.warnings.slice(0, 5).map((w, i) => <div key={i}>{w}</div>)} />
          )}
          <ReportView report={result.report} />
        </>
      )}
    </Space>
  )
}

export function ImportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const refresh = useStore((s) => s.refresh)
  return (
    <Modal open={open} onCancel={onClose} footer={null} width={720} title="导入 / 采集数据流">
      <Tabs
        items={[
          { key: 'excel', label: 'Excel 台账', children: <ExcelTab onDone={refresh} /> },
          { key: 'sql', label: 'SQL 血缘', children: <SqlTab onDone={refresh} /> },
          { key: 'db', label: '数据库采集', children: <DbTab onDone={refresh} /> },
        ]}
      />
    </Modal>
  )
}
