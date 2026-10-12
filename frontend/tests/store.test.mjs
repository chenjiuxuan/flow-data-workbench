/** 加载实际 TypeScript store；模拟独立持久化服务器与交错网络响应。 */
import assert from 'node:assert/strict'
import { after, afterEach, test } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
const { createGraphStore } = await vite.ssrLoadModule('/src/store.ts')
const { nodeBounds } = await vite.ssrLoadModule('/src/utils/bounds.ts')
const { portForDatabaseChange } = await vite.ssrLoadModule('/src/utils/database.ts')
const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
after(async () => { await vite.close() })

const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const clone = (data) => structuredClone(data)
const node = (id, type = 'system', extra = {}) => ({
  id, type, name: id, attrs: {}, origin: 'manual', vanished: false, x: 20, y: 40,
  manualFields: [], provenance: {}, ...extra,
})
const edge = (id, source, target, extra = {}) => ({
  id, source, target, type: 'flow', label: id, attrs: {}, origin: 'manual', vanished: false,
  manualFields: [], provenance: {}, ...extra,
})
const fixture = () => ({
  revision: 'r0',
  nodes: [
    node('sys:A', 'system', { attrs: { owner: 'Alice' }, origin: 'auto',
      provenance: { sources: ['excel:book'], source_states: { 'excel:book': { active: true } }, manual_fields: [] } }),
    node('sys:B'), node('tbl:F', 'table'), node('tbl:hidden', 'table', { vanished: true }),
  ],
  edges: [edge('flow:AB', 'sys:A', 'sys:B'), edge('cross-view', 'sys:A', 'tbl:F'), edge('hidden-edge', 'tbl:F', 'tbl:hidden', { vanished: true })],
})

function mockServer(initial = fixture()) {
  let state = clone(initial)
  let revision = 0
  const calls = []
  const server = {
    calls, beforeRestore: null, failNextRestore: false,
    snapshot: () => clone(state),
    mutate: (change) => { change(state); state.revision = `r${++revision}` },
    writes: () => calls.filter((call) => call.path === '/api/import/json'),
  }
  globalThis.fetch = async (url, init = {}) => {
    const path = String(url)
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ path, method: init.method ?? 'GET', body })
    if (path === '/api/export/json') return response(state)
    if (path.startsWith('/api/trace/')) return response({ nodes: state.nodes.filter((n) => !n.vanished), edges: state.edges.filter((e) => !e.vanished) })
    if (path.startsWith('/api/graph?view=')) {
      const view = new URL(path, 'http://test').searchParams.get('view')
      const types = view === 'field' ? ['table'] : view === 'business' ? ['process_step', 'system'] : ['system', 'database', 'api', 'file', 'external']
      const nodes = state.nodes.filter((n) => !n.vanished && types.includes(n.type))
      const ids = new Set(nodes.map((n) => n.id))
      return response({ nodes, edges: state.edges.filter((e) => !e.vanished && ids.has(e.source) && ids.has(e.target)) })
    }
    if (path === '/api/import/json') {
      if (server.beforeRestore) await server.beforeRestore(body)
      if (server.failNextRestore) { server.failNextRestore = false; return response({ detail: 'simulated write failure' }, 500) }
      if (body.expected_revision !== state.revision) return response({ detail: '图已经被其他操作修改，请刷新后重试' }, 409)
      assert.equal(body.mode, 'replace')
      state = { nodes: clone(body.nodes), edges: clone(body.edges), revision: `r${++revision}` }
      return response({ restored_nodes: state.nodes.length, restored_edges: state.edges.length, revision: state.revision })
    }
    throw new Error(`Unexpected request: ${path}`)
  }
  return server
}

test('选择、加载、追溯、刷新和切视图均不进入历史，也不消耗 redo', async () => {
  const server = mockServer()
  const store = createGraphStore()
  await store.getState().refresh()
  store.getState().select({ kind: 'node', id: 'sys:A' })
  store.getState().setCanvasSelection(['sys:A', 'sys:B'], [])
  await store.getState().runTrace('sys:A', 'downstream')
  store.getState().clearTrace()
  await store.getState().setView('field')
  await store.getState().refresh()
  assert.equal(store.getState().past.length, 0)
  assert.equal(server.writes().length, 0)
  await store.getState().patchNode('sys:A', { name: 'edited', attrs: {} })
  assert.equal(store.getState().past.length, 1)
  await store.getState().undo()
  await store.getState().setView('business')
  store.getState().select(null)
  await store.getState().refresh()
  assert.equal(store.getState().past.length, 0)
  assert.equal(store.getState().future.length, 1)
  await store.getState().redo()
  assert.equal(server.snapshot().nodes.find((n) => n.id === 'sys:A').name, 'edited')
})

test('逆序的 setView 和 refresh 响应不能覆盖最新视图及 loading', async () => {
  const pending = []
  globalThis.fetch = (url) => new Promise((resolve) => pending.push({ url, resolve }))
  const store = createGraphStore()
  const oldRefresh = store.getState().refresh()
  const business = store.getState().setView('business')
  const field = store.getState().setView('field')
  pending[2].resolve(response({ nodes: [node('tbl:current', 'table')], edges: [] }))
  await field
  pending[1].resolve(response({ nodes: [node('sys:stale-business')], edges: [] }))
  pending[0].resolve(response({ nodes: [node('sys:stale-refresh')], edges: [] }))
  await Promise.all([oldRefresh, business])
  assert.equal(store.getState().view, 'field')
  assert.deepEqual(store.getState().nodes.map((n) => n.id), ['tbl:current'])
  assert.equal(store.getState().loading, false)
  assert.equal(store.getState().past.length, 0)

  const older = store.getState().refresh()
  const newer = store.getState().refresh()
  pending[4].resolve(response({ nodes: [node('tbl:newest', 'table')], edges: [] }))
  await newer
  pending[3].resolve(response({ nodes: [node('tbl:stale', 'table')], edges: [] }))
  await older
  assert.deepEqual(store.getState().nodes.map((n) => n.id), ['tbl:newest'])
})

test('删除、undo、redo 持久化整个图，包含隐藏项、跨视图边和来源状态', async () => {
  const server = mockServer()
  const initial = server.snapshot()
  const store = createGraphStore()
  await store.getState().refresh()
  await store.getState().deleteItems(['sys:A'])
  assert.equal(server.snapshot().nodes.some((n) => n.id === 'sys:A'), false)
  assert.equal(server.snapshot().edges.some((e) => e.id === 'cross-view'), false)
  assert.equal(store.getState().past[0].before.nodes.length, 4)
  assert.equal(store.getState().past[0].before.edges.length, 3)
  await store.getState().undo()
  await store.getState().refresh()
  assert.deepEqual(server.snapshot().nodes, initial.nodes)
  assert.deepEqual(server.snapshot().edges, initial.edges)
  assert.equal(store.getState().nodes.some((n) => n.id === 'sys:A'), true)
  await store.getState().redo()
  await store.getState().refresh()
  assert.equal(store.getState().nodes.some((n) => n.id === 'sys:A'), false)
  assert.equal(server.snapshot().nodes.some((n) => n.id === 'tbl:hidden' && n.vanished), true)
  assert.equal(server.writes().length, 3)
  assert.deepEqual(server.writes().map((call) => call.body.expected_revision), ['r0', 'r1', 'r2'])
})

test('跨视图撤销布局保持当前字段视图，不恢复旧投影', async () => {
  const server = mockServer()
  const store = createGraphStore()
  await store.getState().refresh()
  await store.getState().saveLayout({ 'sys:A': { x: 340, y: 290 } })
  await store.getState().setView('field')
  await store.getState().undo()
  assert.equal(store.getState().view, 'field')
  assert.deepEqual(store.getState().nodes.map((n) => n.id), ['tbl:F'])
  assert.equal(server.snapshot().nodes.find((n) => n.id === 'sys:A').x, 20)
  await store.getState().redo()
  assert.equal(store.getState().view, 'field')
  assert.deepEqual(store.getState().nodes.map((n) => n.id), ['tbl:F'])
  assert.equal(server.snapshot().nodes.find((n) => n.id === 'sys:A').x, 340)
})

test('CAS 冲突保留历史和外部数据；失败写入不产生幽灵编辑，队列可以继续', async () => {
  const server = mockServer()
  const store = createGraphStore()
  await store.getState().refresh()
  await store.getState().patchNode('sys:A', { name: 'mine', attrs: {} })
  const past = store.getState().past
  server.mutate((backup) => { backup.nodes.find((n) => n.id === 'sys:A').name = 'external' })
  await assert.rejects(store.getState().undo(), (error) => error.status === 409)
  assert.equal(store.getState().past, past)
  assert.equal(store.getState().future.length, 0)
  assert.equal(server.snapshot().nodes.find((n) => n.id === 'sys:A').name, 'external')
  server.failNextRestore = true
  await assert.rejects(store.getState().deleteItems(['sys:B']), /simulated write failure/)
  assert.equal(store.getState().past, past)
  assert.equal(server.snapshot().nodes.some((n) => n.id === 'sys:B'), true)
  assert.equal(store.getState().busy, false)
  await store.getState().patchNode('sys:A', { name: 'after-conflict', attrs: {} })
  assert.equal(store.getState().past.length, 1)
  await store.getState().undo()
  assert.equal(server.snapshot().nodes.find((n) => n.id === 'sys:A').name, 'external')
})

test('attrs null 清除、人工保护标记与 provenance 均可完整撤销/重做；无变化不入栈', async () => {
  const server = mockServer()
  const original = server.snapshot().nodes.find((n) => n.id === 'sys:A')
  const store = createGraphStore()
  await store.getState().patchNode('sys:A', { name: 'sys:A', attrs: { owner: null, desc: 'new description' } })
  const changed = server.snapshot().nodes.find((n) => n.id === 'sys:A')
  assert.equal('owner' in changed.attrs, false)
  assert.deepEqual(changed.manualFields, ['desc', 'owner'])
  assert.deepEqual(changed.provenance.source_states, original.provenance.source_states)
  assert.deepEqual(changed.provenance.manual_fields, ['desc', 'owner'])
  assert.equal(await store.getState().patchNode('sys:A', { name: 'sys:A', attrs: { desc: 'new description' } }), false)
  assert.equal(store.getState().past.length, 1)
  await store.getState().undo()
  assert.deepEqual(server.snapshot().nodes.find((n) => n.id === 'sys:A'), original)
  await store.getState().redo()
  assert.deepEqual(server.snapshot().nodes.find((n) => n.id === 'sys:A'), changed)
})

test('建点、建边、多选删除均为单次 CAS 编辑；失败不会部分删除', async () => {
  const server = mockServer()
  const store = createGraphStore()
  await store.getState().createNode({ type: 'system', name: 'new system', x: 55, y: 75 })
  const added = server.snapshot().nodes.find((n) => n.name === 'new system')
  await store.getState().createEdge({ source: 'sys:B', target: added.id, type: 'flow', label: 'new edge' })
  const connection = server.snapshot().edges.find((e) => e.label === 'new edge')
  await store.getState().undo()
  assert.equal(server.snapshot().edges.some((e) => e.id === connection.id), false)
  await store.getState().redo()
  assert.equal(server.snapshot().edges.some((e) => e.id === connection.id), true)
  await assert.rejects(store.getState().createEdge({ source: 'sys:B', target: added.id, type: 'flow', label: 'new edge' }), /已存在/)
  const writes = server.writes().length
  await store.getState().deleteItems(['sys:A', added.id], ['hidden-edge'])
  assert.equal(server.writes().length, writes + 1)
  assert.deepEqual(server.snapshot().nodes.map((n) => n.id), ['sys:B', 'tbl:F', 'tbl:hidden'])
  assert.deepEqual(server.snapshot().edges, [])
  await store.getState().undo()
  assert.equal(server.snapshot().edges.some((e) => e.id === 'cross-view'), true)
  assert.equal(server.snapshot().edges.some((e) => e.id === connection.id), true)
})

test('编辑与外部导入序列执行，导入后布置新节点且只在真实写入后清历史', async () => {
  const server = mockServer()
  const store = createGraphStore()
  let release
  let started
  const gate = new Promise((resolve) => { release = resolve })
  const entered = new Promise((resolve) => { started = resolve })
  server.beforeRestore = async () => { server.beforeRestore = null; started(); await gate }
  const layout = store.getState().saveLayout({ 'sys:A': { x: 120, y: 160 } })
  const patch = store.getState().patchNode('sys:A', { name: 'queued-name', attrs: {} })
  let externalStarted = false
  const imported = store.getState().runExternal(async () => {
    externalStarted = true
    assert.equal(server.snapshot().nodes.find((n) => n.id === 'sys:A').name, 'queued-name')
    server.mutate((backup) => { backup.nodes.push(node('physical-tbl:db:new', 'table', { x: 0, y: 0 })) })
    return { applied: true }
  }, { view: () => 'field' })
  await entered
  assert.equal(store.getState().busy, true)
  assert.equal(externalStarted, false)
  assert.equal(server.calls.filter((call) => call.path === '/api/export/json').length, 1)
  release()
  await Promise.all([layout, patch, imported])
  assert.equal(store.getState().busy, false)
  assert.equal(store.getState().past.length, 0)
  assert.equal(store.getState().future.length, 0)
  assert.equal(store.getState().view, 'field')
  const physical = server.snapshot().nodes.find((n) => n.id === 'physical-tbl:db:new')
  assert.ok(physical.x !== 0 || physical.y !== 0)
  assert.equal(await store.getState().undo(), false)
})

test('失败导入、report:null 未apply 和幂等操作保留既有 undo/redo 历史', async () => {
  const server = mockServer()
  const store = createGraphStore()
  await store.getState().patchNode('sys:A', { name: 'edited', attrs: {} })
  const past = store.getState().past
  const writes = server.writes().length
  await assert.rejects(store.getState().runExternal(async () => { throw new Error('parse rejected') }), /parse rejected/)
  assert.equal(store.getState().past, past)
  await store.getState().runExternal(async () => ({ report: null, errors: ['invalid SQL'] }), { applied: (result) => result.report !== null })
  assert.equal(store.getState().past, past)
  assert.equal(server.writes().length, writes)
  await store.getState().undo()
  const future = store.getState().future
  await store.getState().runExternal(async () => ({ report: null }), { applied: () => false })
  assert.equal(store.getState().future, future)
  await store.getState().redo()
  assert.equal(server.snapshot().nodes.find((n) => n.id === 'sys:A').name, 'edited')
})

test('失败操作如果实际部分写入则刷新画布并清旧历史，避免旧快照复盖数据', async () => {
  const server = mockServer()
  const store = createGraphStore()
  await store.getState().patchNode('sys:A', { name: 'edited', attrs: {} })
  await assert.rejects(store.getState().runExternal(async () => {
    server.mutate((backup) => { backup.nodes.push(node('sys:partial')) })
    throw new Error('response lost after commit')
  }), /response lost/)
  assert.equal(store.getState().past.length, 0)
  assert.equal(store.getState().nodes.some((n) => n.id === 'sys:partial'), true)
  assert.equal(await store.getState().undo(), false)
})

test('初始化处理堆叠布局且不入历史；未apply的错误导入不会触发布局写入', async () => {
  const initial = fixture()
  initial.nodes.forEach((n) => { n.x = 0; n.y = 0 })
  const server = mockServer(initial)
  const store = createGraphStore()
  await store.getState().runExternal(async () => ({ report: null }), { applied: () => false })
  assert.equal(server.writes().length, 0)
  await store.getState().initialize()
  assert.notDeepEqual(server.snapshot().nodes[0].x, server.snapshot().nodes[1].x)
  assert.equal(store.getState().past.length, 0)
  const writes = server.writes().length
  await store.getState().refresh()
  await store.getState().refresh()
  assert.equal(server.writes().length, writes)
})

test('清除追溯与切视图后，迟到的 trace 响应不能重新出现', async () => {
  const server = mockServer()
  const fetch = globalThis.fetch
  let finishTrace
  globalThis.fetch = (url, init) => String(url).startsWith('/api/trace/')
    ? new Promise((resolve) => { finishTrace = resolve }) : fetch(url, init)
  const store = createGraphStore()
  const trace = store.getState().runTrace('sys:A', 'downstream')
  store.getState().clearTrace()
  await store.getState().setView('field')
  finishTrace(response(server.snapshot()))
  await trace
  assert.equal(store.getState().trace, null)
})

test('切换数据库类型更新缺省端口，往返切换仍使用当前类型缺省值', () => {
  const postgresPort = portForDatabaseChange('mysql', 'postgresql', 3306)
  assert.equal(postgresPort, 5432)
  assert.equal(portForDatabaseChange('postgresql', 'mysql', postgresPort), 3306)
  assert.equal(portForDatabaseChange('mysql', 'postgresql', null), 5432)
  assert.equal(portForDatabaseChange('postgresql', 'mysql', undefined), 3306)
})

test('切换数据库类型保留非缺省的自定义端口', () => {
  assert.equal(portForDatabaseChange('mysql', 'postgresql', 13306), 13306)
  assert.equal(portForDatabaseChange('postgresql', 'mysql', 15432), 15432)
})

test('大图边界迭代处理，负坐标与极值准确且不超过函数参数上限', () => {
  const nodes = Array.from({ length: 250_000 }, (_, i) => ({ x: i - 100_000, y: 0 - i }))
  assert.deepEqual(nodeBounds(nodes), { minX: -100_000, minY: -249_999, maxX: 149_999, maxY: 0 })
  assert.equal(nodeBounds([]), null)
})
