/** 使用真实流程 store 与 HTTP 契约验证交错请求、CAS 及文档隔离。 */
import assert from 'node:assert/strict'
import { after, afterEach, test } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom', logLevel: 'error' })
const { createProcessStore } = await vite.ssrLoadModule('/src/process/store.ts')
const { approvalProcess, emptyProcess } = await vite.ssrLoadModule('/src/process/templates.ts')
const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
after(async () => { await vite.close() })

const clone = (data) => structuredClone(data)
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
const node = (id, name = id) => ({ id, name, type: 'task', x: 10, y: 20, owner: '', description: '', input: '', output: '', assetIds: [] })
const diagram = (id) => ({
  id, name: `流程 ${id}`, description: `说明 ${id}`, revision: `${id}:0`,
  nodeCount: 1, edgeCount: 0, updatedAt: '2026-10-12T00:00:00Z', nodes: [node(`${id}-1`)], edges: [],
})
const summary = ({ nodes: _nodes, edges: _edges, ...data }) => data

test('目录和资产请求的失败与重试不覆盖文档保存冲突', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().open('A')
  server.mutate('A', (value) => { value.nodes[0].name = '其他页面的修改' })
  await assert.rejects(store.getState().edit((draft) => { draft.nodes[0].name = '本页修改' }))
  const conflict = store.getState().error
  assert.ok(conflict)
  server.beforeAssets = () => { throw new Error('资产读取失败') }
  await assert.rejects(store.getState().loadAssets(), /资产读取失败/)
  assert.equal(store.getState().error, conflict)
  server.beforeAssets = null
  await store.getState().loadAssets()
  await store.getState().loadList()
  assert.equal(store.getState().error, conflict)
  assert.equal(store.getState().current.nodes[0].name, 'A-1')
  await store.getState().open('A')
  assert.equal(store.getState().error, null)
  assert.equal(store.getState().current.nodes[0].name, '其他页面的修改')
})

function mockServer() {
  const diagrams = new Map(['A', 'B'].map((id) => [id, diagram(id)]))
  let sequence = 0
  const calls = []
  const server = {
    calls, beforeGet: null, beforeList: null, beforePut: null, beforeAssets: null, failNextWrite: false,
    snapshot: (id) => clone(diagrams.get(id)),
    all: () => [...diagrams.keys()],
    writes: () => calls.filter((call) => call.method === 'PUT'),
    mutate: (id, change) => {
      const current = diagrams.get(id)
      change(current)
      current.revision = `${id}:${++sequence}`
    },
  }
  globalThis.fetch = async (url, init = {}) => {
    const parsed = new URL(String(url), 'http://test')
    const path = parsed.pathname
    const method = init.method ?? 'GET'
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ path, method, body })
    if (path === '/api/assets') {
      return response(server.beforeAssets ? await server.beforeAssets() : [{ id: 'sys:shared', type: 'system', name: '共享系统' }])
    }
    if (path === '/api/diagrams' && method === 'GET') {
      const result = [...diagrams.values()].map((item) => summary(clone(item)))
      if (server.beforeList) await server.beforeList()
      return response(result)
    }
    if (path === '/api/diagrams' && method === 'POST') {
      const id = `created-${++sequence}`
      const result = { ...clone(body), id, revision: `${id}:0`, nodeCount: body.nodes.length, edgeCount: body.edges.length, updatedAt: '2026-10-12T00:01:00Z' }
      diagrams.set(id, result)
      return response(result)
    }
    if (path.startsWith('/api/diagrams/')) {
      const id = decodeURIComponent(path.slice('/api/diagrams/'.length))
      const current = diagrams.get(id)
      if (!current) return response({ detail: '流程不存在' }, 404)
      if (method === 'GET') {
        const result = clone(current)
        if (server.beforeGet) await server.beforeGet(id)
        return response(result)
      }
      if (method === 'PUT' && server.beforePut) await server.beforePut(body)
      if (server.failNextWrite) { server.failNextWrite = false; return response({ detail: '模拟保存失败' }, 500) }
      const expected = method === 'DELETE' ? parsed.searchParams.get('expected_revision') : body.expected_revision
      if (expected !== current.revision) return response({ detail: '流程已在其他窗口更新，请重新打开后重试' }, 409)
      if (method === 'DELETE') { diagrams.delete(id); return response({ ok: true }) }
      if (method === 'PUT') {
        const { expected_revision: _revision, ...desired } = clone(body)
        const result = { ...desired, id, revision: `${id}:${++sequence}`, nodeCount: desired.nodes.length, edgeCount: desired.edges.length, updatedAt: '2026-10-12T00:02:00Z' }
        diagrams.set(id, result)
        return response(result)
      }
    }
    throw new Error(`Unexpected request: ${method} ${path}`)
  }
  return server
}

test('每个文档保存独立撤销历史，外部 revision 变化后丢弃旧历史', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().open('A')
  await store.getState().edit((draft) => { draft.nodes[0].name = 'A 已编辑' })
  await store.getState().open('B')
  assert.equal(store.getState().past.length, 0)
  await store.getState().edit((draft) => { draft.nodes[0].name = 'B 已编辑' })
  await store.getState().open('A')
  assert.equal(store.getState().past.length, 1)
  await store.getState().undo()
  assert.equal(server.snapshot('A').nodes[0].name, 'A-1')
  assert.equal(server.snapshot('B').nodes[0].name, 'B 已编辑')
  await store.getState().open('B')
  assert.equal(store.getState().past.length, 1)
  server.mutate('B', (value) => { value.nodes[0].name = '另一窗口修改' })
  await store.getState().open('B')
  assert.equal(store.getState().past.length, 0)
  assert.equal(store.getState().future.length, 0)
  assert.equal(store.getState().current.nodes[0].name, '另一窗口修改')
})

test('迟到打开响应不能覆盖最新文档；关闭会取消等待中的打开', async () => {
  const server = mockServer()
  const waits = new Map([['A', deferred()], ['B', deferred()]])
  server.beforeGet = (id) => waits.get(id).promise
  const store = createProcessStore()
  const openA = store.getState().open('A')
  const openB = store.getState().open('B')
  waits.get('B').resolve()
  await openB
  assert.equal(store.getState().current.id, 'B')
  assert.equal(store.getState().loading, false)
  waits.get('A').resolve()
  await openA
  assert.equal(store.getState().current.id, 'B')
  const wait = deferred()
  server.beforeGet = () => wait.promise
  const reopening = store.getState().open('A')
  store.getState().close()
  wait.resolve()
  await reopening
  assert.equal(store.getState().current, null)
  assert.equal(store.getState().loading, false)
})

test('保存队列基于最近成功内容构建，写入期间不能切图或关闭', async () => {
  const server = mockServer()
  const gate = deferred()
  let entered = deferred()
  server.beforePut = async () => { entered.resolve(); await gate.promise }
  const store = createProcessStore()
  await store.getState().open('A')
  const first = store.getState().edit((draft) => { draft.nodes.push(node('A-2')) })
  const second = store.getState().edit((draft) => { draft.nodes.push(node('A-3')) })
  await entered.promise
  assert.equal(store.getState().busy, true)
  await store.getState().open('B')
  store.getState().close()
  assert.equal(store.getState().current.id, 'A')
  assert.equal(server.writes().length, 1)
  server.beforePut = null
  gate.resolve()
  assert.deepEqual(await Promise.all([first, second]), [true, true])
  assert.deepEqual(server.snapshot('A').nodes.map((item) => item.id), ['A-1', 'A-2', 'A-3'])
  assert.equal(server.writes()[1].body.expected_revision, 'A:1')
  assert.equal(store.getState().past.length, 2)
  assert.equal(store.getState().busy, false)
})

test('失败及 CAS 冲突保留已保存内容与历史，后续操作仍可执行', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().open('A')
  server.failNextWrite = true
  const failed = store.getState().edit((draft) => { draft.nodes[0].name = '不应保留' })
  const succeeding = store.getState().edit((draft) => { draft.nodes[0].owner = '流程负责人' })
  await assert.rejects(failed, /模拟保存失败/)
  await succeeding
  assert.equal(store.getState().current.nodes[0].name, 'A-1')
  assert.equal(store.getState().current.nodes[0].owner, '流程负责人')
  assert.equal(store.getState().past.length, 1)
  const before = clone(store.getState().current)
  server.mutate('A', (value) => { value.nodes[0].name = '外部最新内容' })
  await assert.rejects(store.getState().edit((draft) => { draft.nodes[0].name = '旧页修改' }), (error) => error.status === 409)
  assert.deepEqual(store.getState().current, before)
  assert.equal(store.getState().past.length, 1)
  await assert.rejects(store.getState().undo(), (error) => error.status === 409)
  assert.equal(store.getState().past.length, 1)
  assert.equal(store.getState().future.length, 0)
  assert.match(store.getState().error, /其他窗口/)
  assert.equal(server.snapshot('A').nodes[0].name, '外部最新内容')
  await store.getState().open('A')
  assert.equal(store.getState().error, null)
  assert.equal(store.getState().past.length, 0)
})

test('重命名后的撤销和重做只改变内容，保留名称与说明', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().loadList()
  await store.getState().open('A')
  await store.getState().edit((draft) => { draft.nodes[0].name = '修改后环节' })
  await store.getState().rename('A', '新流程名', '新说明')
  assert.equal(store.getState().past.length, 1)
  await store.getState().undo()
  assert.equal(server.snapshot('A').name, '新流程名')
  assert.equal(server.snapshot('A').description, '新说明')
  assert.equal(server.snapshot('A').nodes[0].name, 'A-1')
  await store.getState().rename('B', '另一流程新名', '另一说明')
  await store.getState().redo()
  assert.equal(server.snapshot('A').name, '新流程名')
  assert.equal(server.snapshot('A').nodes[0].name, '修改后环节')
  assert.equal(server.snapshot('B').name, '另一流程新名')
})

test('删除流程只影响指定文档，失败时目录、选择与历史保持原样', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().loadList()
  await store.getState().loadAssets()
  await store.getState().open('A')
  await store.getState().edit((draft) => { draft.nodes[0].name = '已保存' })
  store.getState().select({ kind: 'node', id: 'A-1' })
  server.failNextWrite = true
  await assert.rejects(store.getState().remove('A'), /保存失败/)
  assert.equal(store.getState().diagrams.length, 2)
  assert.equal(store.getState().current.id, 'A')
  assert.equal(store.getState().past.length, 1)
  assert.equal(store.getState().selection.id, 'A-1')
  await store.getState().remove('B')
  assert.equal(store.getState().current.id, 'A')
  assert.equal(store.getState().past.length, 1)
  assert.equal(store.getState().selection.id, 'A-1')
  assert.deepEqual(server.all(), ['A'])
  await store.getState().remove('A')
  assert.equal(store.getState().current, null)
  assert.equal(store.getState().past.length, 0)
  assert.equal(store.getState().diagrams.length, 0)
  assert.equal(store.getState().assets.length, 1)
})

test('旧目录查询不会抹掉刚创建的流程，资产只采用最近一次查询结果', async () => {
  const server = mockServer()
  const listWait = deferred()
  server.beforeList = () => listWait.promise
  const store = createProcessStore()
  const list = store.getState().loadList()
  const created = await store.getState().create(emptyProcess('新建流程'))
  listWait.resolve()
  await list
  assert.equal(store.getState().current.id, created.id)
  assert.ok(store.getState().diagrams.some((item) => item.id === created.id))
  const assetWaits = [deferred(), deferred()]
  let index = 0
  server.beforeAssets = () => assetWaits[index++].promise
  const old = store.getState().loadAssets()
  const latest = store.getState().loadAssets()
  assetWaits[1].resolve([{ id: 'latest' }])
  await latest
  assetWaits[0].resolve([{ id: 'stale' }])
  await old
  assert.deepEqual(store.getState().assets, [{ id: 'latest' }])
  assert.equal(store.getState().current.id, created.id)
})

test('写入途中发出的目录查询不能回滚已保存的名称与 revision', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().loadList()
  await store.getState().open('A')
  const writing = deferred()
  const entered = deferred()
  const listing = deferred()
  server.beforePut = async () => { entered.resolve(); await writing.promise }
  server.beforeList = () => listing.promise
  const rename = store.getState().rename('A', '最新名称', '最新说明')
  await entered.promise
  const list = store.getState().loadList()
  writing.resolve()
  await rename
  listing.resolve()
  await list
  const saved = store.getState().diagrams.find((item) => item.id === 'A')
  assert.equal(saved.name, '最新名称')
  assert.equal(saved.revision, server.snapshot('A').revision)
  assert.equal(store.getState().loading, false)
})

test('无变化编辑不写入，历史最多保留一百步', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().open('A')
  assert.equal(await store.getState().edit(() => {}), false)
  assert.equal(server.writes().length, 0)
  for (let i = 1; i <= 105; i += 1) {
    await store.getState().edit((draft) => { draft.nodes[0].name = `步骤 ${i}` })
  }
  assert.equal(store.getState().past.length, 100)
  assert.equal(store.getState().past[0].nodes[0].name, '步骤 5')
})

test('审批示例包含判断分支和退回环路，所有引用都是模板内部节点', () => {
  const process = approvalProcess()
  const nodes = new Map(process.nodes.map((item) => [item.id, item]))
  assert.ok(process.nodes.some((item) => item.type === 'start'))
  assert.ok(process.nodes.some((item) => item.type === 'end'))
  assert.ok(process.nodes.every((item) => item.assetIds.length === 0))
  assert.ok(process.edges.every((item) => nodes.has(item.source) && nodes.has(item.target) && item.targetHandle === 'in'))
  const decision = process.nodes.find((item) => item.type === 'decision')
  assert.deepEqual(process.edges.filter((item) => item.source === decision.id).map((item) => item.sourceHandle).sort(), ['no', 'yes'])
  const returned = process.edges.find((item) => item.label === '不通过').target
  assert.equal(process.edges.find((item) => item.source === returned).target, 'review')
  const main = ['start', 'submit', 'review', 'decision', 'execute', 'end'].map((id) => nodes.get(id))
  assert.ok(main.every((item, index) => index === 0 || item.x > main[index - 1].x))
  assert.ok(nodes.get(returned).y > Math.max(...main.map((item) => item.y)) + 150)
  assert.ok(nodes.get('end').x + 128 - nodes.get('start').x < 1300)
})
