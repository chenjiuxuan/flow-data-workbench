/** 导入与采集对话框：Excel 台账 / SQL 血缘 / 数据库采集 三个入口。 */
import { DownloadOutlined, DatabaseOutlined, FileExcelOutlined, ThunderboltOutlined } from '@ant-design/icons'
import {
  Alert, App as AntdApp, Button, Form, Input, InputNumber, Modal, Select, Space, Table, Tabs, Typography, Upload,
} from 'antd'
import { useRef, useState } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'
import type { MergeReport, ViewKey } from '../types'
import { DEFAULT_DATABASE_PORT, portForDatabaseChange, type DatabaseType } from '../utils/database'

const { Text } = Typography
const { TextArea } = Input

function ReportView({ report, warnings = false }: { report: MergeReport | null; warnings?: boolean }) {
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
        type={warnings ? 'warning' : 'success'}
        message={warnings ? '已并入可用结果，请检查提示' : '并入完成'}
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
    </div>
  )
}

/** 导入结果如果落在别的视图（如 SQL 血缘进字段视图），自动切过去让用户立刻看到。 */
function importView(report: MergeReport | null): ViewKey | undefined {
  const ids = [...(report?.added_nodes ?? []), ...(report?.updated_nodes ?? [])]
  const hasTable = ids.some((id) => id.startsWith('tbl:') || id.startsWith('physical-tbl:'))
  const hasSystem = ids.some((id) => id.startsWith('sys:') || id.startsWith('db:'))
  if (hasTable) return 'field'
  if (hasSystem && useStore.getState().view === 'field') return 'system'
}

function ImportErrors({ errors }: { errors: string[] }) {
  if (!errors.length) return null
  return <Alert type="error" showIcon message="存在错误，未写入图，请修正后重试"
    description={errors.slice(0, 5).map((error, i) => <div key={i}>{error}</div>)} />
}

function ExcelTab() {
  const { message } = AntdApp.useApp()
  const busy = useStore((s) => s.busy)
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
          disabled={busy}
          showUploadList={false}
          customRequest={async ({ file, onSuccess, onError }) => {
            try {
              const r = await useStore.getState().runExternal(() => api.importExcel(file as File, true), {
                view: (result) => importView(result.report), applied: (result) => result.report !== null,
              })
              setResult(<><ImportErrors errors={r.errors} /><ReportView report={r.report} /></>)
              onSuccess?.(r)
              if (r.errors.length) message.error('存在错误，未写入图，请修正后重试')
              else if (r.report) message.success('Excel 已导入')
            } catch (e) {
              onError?.(e as Error)
              message.error(`导入失败：${(e as Error).message}`)
            }
          }}
        >
          <Button type="primary" disabled={busy} icon={<FileExcelOutlined />}>上传 Excel 并入图</Button>
        </Upload>
      </Space>
      {result}
    </Space>
  )
}

function SqlTab() {
  const { message } = AntdApp.useApp()
  const [form] = Form.useForm()
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.importSql>> | null>(null)
  const [loading, setLoading] = useState(false)
  const busy = useStore((s) => s.busy)

  const run = async (apply: boolean) => {
    try {
      const values = await form.validateFields()
      setLoading(true)
      const r = apply
        ? await useStore.getState().runExternal(() => api.importSql({ ...values, apply }), {
            view: (result) => importView(result.report), applied: (result) => result.report !== null,
          })
        : await api.importSql({ ...values, apply })
      setResult(r)
      if (r.errors.length) {
        message.error('存在错误，未写入图，请修正后重试')
      } else if (apply && r.report) {
        if (r.warnings?.length) message.warning('SQL 血缘已并入图，请检查字段解析提示')
        else message.success('SQL 血缘已并入图（可在字段视图查看）')
      } else {
        message.info(`解析出 ${Object.keys(r.tables).length} 张表、${r.edges.length} 条血缘（未入图）`)
      }
    } catch (e) {
      if (e instanceof Error) message.error(`解析失败：${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Text type="secondary">
        粘贴建表/同步/加工作业 SQL，解析表级及可确定的字段血缘。MERGE 目前提供表级血缘；未知结构的 * 需要补充表结构后确认字段。
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
          <Button type="primary" loading={loading} disabled={busy} icon={<ThunderboltOutlined />} onClick={() => void run(true)}>解析并入图</Button>
          <Button disabled={busy || loading} onClick={() => void run(false)}>仅预览</Button>
        </Space>
      </Form>
      {result && (
        <>
          <ImportErrors errors={result.errors} />
          {!!result.warnings?.length && (
            <Alert type="warning" showIcon message="部分字段需要确认"
              description={result.warnings.map((warning, i) => <div key={i}>{warning}</div>)} />
          )}
          <ReportView report={result.report} warnings={!!result.warnings?.length} />
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

function DbTab() {
  const { message } = AntdApp.useApp()
  const [form] = Form.useForm()
  const previousDbType = useRef<DatabaseType>('mysql')
  const [loading, setLoading] = useState(false)
  const busy = useStore((s) => s.busy)
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.discoverDb>> | null>(null)

  const run = async () => {
    try {
      const values = await form.validateFields()
      setLoading(true)
      const r = await useStore.getState().runExternal(() => api.discoverDb({ ...values, apply: true }), {
        view: (result) => importView(result.report),
      })
      setResult(r)
      if (r.warnings.length) message.warning(`已采集 ${r.collected.tables} 张表，部分内容需检查提示`)
      else message.success(`已采集 ${r.collected.tables} 张表`)
    } catch (e) {
      if (e instanceof Error) message.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon icon={<DatabaseOutlined />}
        message="只读采集" description="仅读取库表结构（information_schema 级别），不会改动数据库。" />
      <Form form={form} layout="vertical" initialValues={{ db_type: 'mysql', port: DEFAULT_DATABASE_PORT.mysql }} size="small"
        onValuesChange={(changed, values) => {
          if (changed.db_type) {
            const next = changed.db_type as DatabaseType
            form.setFieldValue('port', portForDatabaseChange(previousDbType.current, next, values.port))
            previousDbType.current = next
          }
        }}>
        <Space wrap>
          <Form.Item label="类型" name="db_type" style={{ minWidth: 120 }}>
            <Select options={[{ value: 'mysql', label: 'MySQL' }, { value: 'postgresql', label: 'PostgreSQL' }]} />
          </Form.Item>
          <Form.Item label="主机" name="host" rules={[{ required: true }]}><Input placeholder="127.0.0.1" /></Form.Item>
          <Form.Item label="端口" name="port" rules={[
            { required: true, message: '请填写端口' },
            { type: 'number', min: 1, max: 65535, message: '端口应为 1–65535 的整数' },
          ]}><InputNumber min={1} max={65535} precision={0} /></Form.Item>
          <Form.Item label="用户" name="user" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="密码" name="password"><Input.Password /></Form.Item>
          <Form.Item label="库名" name="database" rules={[{ required: true }]}><Input /></Form.Item>
        </Space>
        <Button type="primary" disabled={busy} loading={loading} onClick={() => void run()}>采集并入图</Button>
      </Form>
      {result && (
        <>
          {result.warnings.length > 0 && (
            <Alert type="warning" showIcon message="部分内容未采集，已保留此前信息"
              description={result.warnings.slice(0, 5).map((w, i) => <div key={i}>{w}</div>)} />
          )}
          <ReportView report={result.report} warnings={result.warnings.length > 0} />
        </>
      )}
    </Space>
  )
}

export function ImportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onCancel={onClose} footer={null} width={720} title="导入 / 采集数据流">
      <Tabs
        items={[
          { key: 'excel', label: 'Excel 台账', children: <ExcelTab /> },
          { key: 'sql', label: 'SQL 血缘', children: <SqlTab /> },
          { key: 'db', label: '数据库采集', children: <DbTab /> },
        ]}
      />
    </Modal>
  )
}
