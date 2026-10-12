/** 右侧属性面板：查看/编辑选中的节点或连线。 */
import { App as AntdApp, Button, Descriptions, Drawer, Form, Input, Popconfirm, Select, Space, Tag, Typography } from 'antd'
import { useEffect } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'
import { NODE_TYPE_META, EDGE_TYPE_LABEL } from './meta'

const { Text } = Typography

const ATTR_FIELDS: { key: string; label: string }[] = [
  { key: 'owner', label: '负责人' },
  { key: 'method', label: '流转方式' },
  { key: 'frequency', label: '频率' },
  { key: 'content', label: '数据内容' },
  { key: 'desc', label: '描述' },
]

export function PropertyPanel() {
  const { message } = AntdApp.useApp()
  const store = useStore()
  const selection = store.selection
  const node = store.selectedNode()
  const edge = store.selectedEdge()
  const [form] = Form.useForm()

  useEffect(() => {
    const target = node ?? edge
    if (target) {
      form.setFieldsValue({
        name: node?.name ?? edge?.label ?? '',
        type: node?.type ?? edge?.type ?? 'flow',
        owner: String(node?.attrs?.owner ?? edge?.attrs?.owner ?? ''),
        method: String(node?.attrs?.method ?? edge?.attrs?.method ?? ''),
        frequency: String(node?.attrs?.frequency ?? edge?.attrs?.frequency ?? ''),
        content: String(node?.attrs?.content ?? edge?.attrs?.content ?? ''),
        desc: String(node?.attrs?.desc ?? edge?.attrs?.desc ?? ''),
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, node, edge])

  // 选中项已被删除时（如导入冲突、别处删除），自动关闭抽屉
  useEffect(() => {
    if (selection && !node && !edge) store.select(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, node, edge])

  const close = () => store.select(null)

  const save = async () => {
    const values = await form.validateFields()
    if (!values.name?.trim()) {
      message.error('名称不能为空')
      return
    }
    // 空字符串以 null 下发，后端删除该属性
    const attrs: Record<string, unknown> = {}
    for (const { key } of ATTR_FIELDS) {
      const v = values[key]
      attrs[key] = v === undefined || String(v).trim() === '' ? null : v
    }
    if (node) {
      await api.patchNode(node.id, { name: values.name.trim(), attrs })
    } else if (edge) {
      await api.patchEdge(edge.id, { label: values.name.trim(), attrs })
    }
    await store.refresh()
    store.select(selection)
    message.success('已保存（人工修改的字段不会再被自动采集覆盖）')
  }

  const remove = async () => {
    if (node) await api.deleteNode(node.id)
    if (edge) await api.deleteEdge(edge.id)
    store.select(null)
    await store.refresh()
  }

  const target = node ?? edge
  const isNode = !!node
  const extra: Record<string, unknown> | undefined = target?.attrs

  return (
    <Drawer
      open={!!selection}
      onClose={close}
      width={380}
      title={node ? '节点属性' : '连线属性'}
      destroyOnHidden
    >
      {target && (
        <Form form={form} layout="vertical" size="small">
          <Form.Item label={isNode ? '名称' : '标签'} name="name" rules={[{ required: true, message: '名称不能为空' }]}>
            <Input />
          </Form.Item>
          {isNode ? (
            <Form.Item label="类型" name="type">
              <Select
                disabled
                options={Object.entries(NODE_TYPE_META).map(([value, m]) => ({ value, label: m.label }))}
              />
            </Form.Item>
          ) : (
            <Form.Item label="连线类型" name="type">
              <Select
                disabled
                options={Object.entries(EDGE_TYPE_LABEL).map(([value, label]) => ({ value, label }))}
              />
            </Form.Item>
          )}
          {ATTR_FIELDS.map(({ key, label }) => (
            <Form.Item key={key} label={label} name={key}>
              <Input allowClear placeholder="留空即清除" />
            </Form.Item>
          ))}

          <Space style={{ marginBottom: 16 }} wrap>
            <Button type="primary" onClick={() => void save()}>保存</Button>
            <Popconfirm title="确定删除？" onConfirm={() => void remove()}>
              <Button danger>删除</Button>
            </Popconfirm>
            {node && (
              <>
                <Button onClick={() => void store.runTrace(node.id, 'upstream')}>追溯上游</Button>
                <Button onClick={() => void store.runTrace(node.id, 'downstream')}>追溯下游</Button>
              </>
            )}
          </Space>

          <Descriptions
            column={1}
            size="small"
            bordered
            title="元信息"
            style={{ marginTop: 8 }}
            items={[
              { key: 'id', label: 'ID', children: <Text code copyable>{target.id}</Text> },
              { key: 'origin', label: '来源', children: target.origin === 'auto' ? <Tag>自动采集</Tag> : <Tag color="blue">人工创建</Tag> },
              ...(target.vanished ? [{ key: 'v', label: '状态', children: <Tag color="red">源中已消失</Tag> }] : []),
              ...(target.manualFields.length
                ? [{ key: 'mf', label: '人工维护字段', children: target.manualFields.map((f) => <Tag key={f}>{f}</Tag>) }]
                : []),
              ...(extra && Array.isArray(extra.fields)
                ? [{
                    key: 'fields',
                    label: '字段映射',
                    children: (
                      <Space direction="vertical" size={0}>
                        {(extra.fields as string[]).map((f, i) => (
                          <Text key={i} style={{ fontSize: 12 }}>{f}</Text>
                        ))}
                      </Space>
                    ),
                  }]
                : []),
              ...(extra && Array.isArray(extra.columns)
                ? [{
                    key: 'cols',
                    label: `字段（${(extra.columns as unknown[]).length}）`,
                    children: (
                      <Space direction="vertical" size={0}>
                        {(extra.columns as { name: string; type: string }[]).map((c) => (
                          <Text key={c.name} style={{ fontSize: 12 }}>{c.name} · {c.type}</Text>
                        ))}
                      </Space>
                    ),
                  }]
                : []),
              ...(extra?.note ? [{ key: 'note', label: '备注', children: String(extra.note) }] : []),
              ...(extra && Array.isArray(extra.statements)
                ? [{
                    key: 'stmt',
                    label: '来源语句',
                    children: (
                      <Space direction="vertical" size={0}>
                        {(extra.statements as string[]).map((s, i) => (
                          <Text key={i} style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{s}</Text>
                        ))}
                      </Space>
                    ),
                  }]
                : []),
            ]}
          />
        </Form>
      )}
    </Drawer>
  )
}
