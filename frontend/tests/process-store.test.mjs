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
      if (server.failNextWrite) { server.failNextWrite = false; return response({ detail: '模拟保存失败' }, 500) }
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

test('新详情确认的名称和 revision 不会被迟到的旧目录回滚', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().loadList()
  const wait = deferred()
  server.beforeList = () => wait.promise
  const oldList = store.getState().loadList()
  server.mutate('A', (value) => { value.name = '其他窗口的新名称' })
  await store.getState().open('A')
  const latest = clone(store.getState().current)
  wait.resolve()
  await oldList
  const listed = store.getState().diagrams.find((item) => item.id === 'A')
  assert.equal(listed.name, latest.name)
  assert.equal(listed.revision, latest.revision)
  assert.equal(store.getState().loading, false)
})

test('后台文档重命名或删除不取消另一文档的待完成打开', async () => {
  for (const operation of ['rename', 'remove']) {
    const server = mockServer()
    const store = createProcessStore()
    await store.getState().loadList()
    const wait = deferred()
    server.beforeGet = (id) => id === 'A' ? wait.promise : undefined
    const opening = store.getState().open('A')
    if (operation === 'rename') await store.getState().rename('B', '后台新名称', '')
    else await store.getState().remove('B')
    assert.equal(store.getState().loading, true)
    wait.resolve()
    await opening
    assert.equal(store.getState().current.id, 'A')
    assert.equal(store.getState().loading, false)
    assert.equal(store.getState().error, null)
  }
})

test('正在打开的同一文档重命名后采用保存响应，旧详情不回滚它', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().loadList()
  const wait = deferred()
  let requests = 0
  server.beforeGet = () => ++requests === 1 ? wait.promise : undefined
  const opening = store.getState().open('A')
  await store.getState().rename('A', '已重命名的流程', '已更新说明')
  assert.equal(store.getState().current.name, '已重命名的流程')
  assert.equal(store.getState().loading, false)
  wait.resolve()
  await opening
  assert.equal(store.getState().current.name, '已重命名的流程')
  assert.equal(store.getState().current.revision, server.snapshot('A').revision)
})

test('新建与删除同一文档会取消旧打开，失败的删除仍允许打开完成', async () => {
  for (const operation of ['create', 'remove', 'failed-remove']) {
    const server = mockServer()
    const store = createProcessStore()
    await store.getState().loadList()
    const wait = deferred()
    server.beforeGet = () => wait.promise
    const opening = store.getState().open('A')
    let created
    if (operation === 'create') created = await store.getState().create(emptyProcess('新流程'))
    else if (operation === 'remove') await store.getState().remove('A')
    else {
      server.failNextWrite = true
      await assert.rejects(store.getState().remove('A'), /模拟保存失败/)
      assert.equal(store.getState().loading, true)
    }
    wait.resolve()
    await opening
    assert.equal(store.getState().current?.id ?? null, operation === 'create' ? created.id : operation === 'remove' ? null : 'A')
    assert.equal(store.getState().loading, false)
    if (operation === 'remove') assert.ok(!store.getState().diagrams.some((item) => item.id === 'A'))
  }
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

test('无操作撤销重做、无变化编辑和后台重命名不会消除当前文档冲突', async () => {
  const server = mockServer()
  const store = createProcessStore()
  await store.getState().loadList()
  await store.getState().open('A')
  server.mutate('A', (value) => { value.nodes[0].name = '其他页面修改' })
  await assert.rejects(store.getState().edit((draft) => { draft.nodes[0].name = '本页修改' }))
  const conflict = store.getState().error
  assert.ok(conflict)
  assert.equal(await store.getState().undo(), false)
  assert.equal(await store.getState().redo(), false)
  assert.equal(await store.getState().edit(() => {}), false)
  await store.getState().rename('B', '另一文档的新名', '')
  assert.equal(store.getState().error, conflict)
  assert.equal(store.getState().current.nodes[0].name, 'A-1')
  await store.getState().open('A')
  assert.equal(store.getState().error, null)
})

test('服务端规范化后的文档名称用于显示和后续保存', async () => {
  const server = mockServer()
  const fetch = globalThis.fetch
  globalThis.fetch = (url, init = {}) => {
    if (init.method === 'POST') {
      const body = JSON.parse(init.body)
      return fetch(url, { ...init, body: JSON.stringify({ ...body, name: body.name.trim() }) })
    }
    return fetch(url, init)
  }
  const store = createProcessStore()
  const created = await store.getState().create(emptyProcess('  规范名称  '))
  assert.equal(created.name, '规范名称')
  assert.equal(store.getState().current.name, '规范名称')
  await store.getState().edit((draft) => { draft.nodes.push(node('new-step')) })
  assert.equal(server.writes().at(-1).body.name, '规范名称')
})

test('后台文档写入与创建失败不替换当前文档的正常或冲突状态', async () => {
  for (const conflict of [false, true]) {
    const server = mockServer()
    const store = createProcessStore()
    await store.getState().loadList()
    await store.getState().open('A')
    if (conflict) {
      server.mutate('A', (value) => { value.nodes[0].name = '另一页面内容' })
      await assert.rejects(store.getState().edit((draft) => { draft.nodes[0].name = '旧页面修改' }))
    }
    const originalError = store.getState().error
    for (const operation of [
      () => store.getState().rename('B', '失败的后台重命名', ''),
      () => store.getState().remove('B'),
      () => store.getState().create(emptyProcess('失败的新流程')),
    ]) {
      server.failNextWrite = true
      await assert.rejects(operation(), /模拟保存失败/)
      assert.equal(store.getState().current.id, 'A')
      assert.equal(store.getState().error, originalError)
      assert.equal(store.getState().busy, false)
    }
    assert.equal(store.getState().diagrams.length, 2)
    assert.equal(store.getState().current.nodes[0].name, 'A-1')
  }
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

test('信息表单和删除确认使用观察到的 revision，目录或当前文档刷新不能绕过 CAS', async () => {
  for (const active of [false, true]) {
    for (const operation of ['rename', 'remove']) {
      const server = mockServer()
      const store = createProcessStore()
      await store.getState().loadList()
      await store.getState().open(active ? 'B' : 'A')
      // 模拟表单或删除确认已打开，此后另一页面更新且本页目录完成刷新。
      const observedRevision = store.getState().diagrams.find((item) => item.id === 'B').revision
      server.mutate('B', (value) => {
        value.name = '另一页面的新名称'
        value.description = '另一页面的新说明'
        value.nodes[0].name = '另一页面的新步骤'
      })
      await store.getState().loadList()
      if (active) await store.getState().open('B')
      const external = server.snapshot('B')
      const submit = () => operation === 'rename'
        ? store.getState().rename('B', '旧表单的名称', '旧表单的说明', observedRevision)
        : store.getState().remove('B', observedRevision)
      await assert.rejects(submit(), (error) => error.status === 409)
      assert.deepEqual(server.snapshot('B'), external)
      assert.equal(store.getState().diagrams.find((item) => item.id === 'B').revision, external.revision)
      assert.equal(store.getState().current.id, active ? 'B' : 'A')
      if (active) assert.equal(store.getState().current.nodes[0].name, '另一页面的新步骤')

      // 用户重新观察最新版本后，使用匹配版本的相同操作可以正常完成。
      if (operation === 'rename') {
        await store.getState().rename('B', '确认后的新名称', '确认后的新说明', external.revision)
        assert.equal(server.snapshot('B').name, '确认后的新名称')
        assert.equal(server.snapshot('B').description, '确认后的新说明')
        assert.equal(server.snapshot('B').nodes[0].name, '另一页面的新步骤')
      } else {
        await store.getState().remove('B', external.revision)
        assert.deepEqual(server.all(), ['A'])
        assert.equal(store.getState().current?.id ?? null, active ? null : 'A')
      }
    }
  }
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
