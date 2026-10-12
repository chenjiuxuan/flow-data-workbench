import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom', logLevel: 'error' })
const { nodeForm, normalizeNodeForm, reconcileDrafts } = await vite.ssrLoadModule('/src/process/formState.ts')
after(async () => { await vite.close() })

const node = { id: 'review', type: 'task', name: '审核申请', owner: '部门负责人', description: '规则', input: '申请单', output: '审核结果', assetIds: ['sys:shared'], x: 10, y: 20 }
const baseline = (value) => JSON.stringify(nodeForm(value))

test('节点名称归一化后可识别成功的无变化保存，同时保留其他属性原文', () => {
  const desired = normalizeNodeForm({ ...nodeForm(node), name: ' 审核申请 ' })
  assert.equal(JSON.stringify(desired), baseline(node))
  assert.deepEqual(normalizeNodeForm({ ...desired, owner: ' 岗位 ', description: ' 规则 ', input: ' 输入 ', output: ' 产出 ' }), {
    ...desired, owner: ' 岗位 ', description: ' 规则 ', input: ' 输入 ', output: ' 产出 ',
  })
})

test('服务端属性改变后真正移除旧草稿，撤销重做不能使已提交节点或连线草稿复活', () => {
  let nodeDrafts = { review: { baseline: baseline(node), value: { ...nodeForm(node), name: '已审核' } } }
  let edgeDrafts = { branch: { baseline: '通过', value: '审核通过' } }
  const saved = { ...node, name: '已审核' }
  nodeDrafts = reconcileDrafts(nodeDrafts, new Map([['review', baseline(saved)]]))
  edgeDrafts = reconcileDrafts(edgeDrafts, new Map([['branch', '审核通过']]))
  nodeDrafts = reconcileDrafts(nodeDrafts, new Map([['review', baseline(node)]]))
  edgeDrafts = reconcileDrafts(edgeDrafts, new Map([['branch', '通过']]))
  assert.deepEqual(nodeDrafts, {})
  assert.deepEqual(edgeDrafts, {})
  assert.deepEqual(reconcileDrafts(nodeDrafts, new Map([['review', baseline(saved)]])), {})
})

test('布局和其他步骤更新保留未提交草稿，删除对应步骤时清理草稿', () => {
  const drafts = { review: { baseline: baseline(node), value: { ...nodeForm(node), name: '待保存名称' } } }
  assert.equal(reconcileDrafts(drafts, new Map([['review', baseline({ ...node, x: 100, y: 300 })], ['other', '已更新']])), drafts)
  assert.deepEqual(reconcileDrafts(drafts, new Map([['other', '已更新']])), {})
})
