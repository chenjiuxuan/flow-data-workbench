/** AI 初稿：错误文案、资产合并、以及未保存编辑不写库。 */
import assert from 'node:assert/strict'
import { after, afterEach, test } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom', logLevel: 'error' })
const { AI_DRAFT_ERROR_MESSAGES, messageForAiDraftCode, validateDraftInput } = await vite.ssrLoadModule('/src/process/ai/messages.ts')
const {
  applyAssetSuggestions, countPendingReview, highlightSource, initialSelectedKeys, orderUncertainties,
  previewEdgeLabel, startBridgeTargetIds, suggestionKey, unreachableNodeIds, UNREACHABLE_EXPLANATION, UNREACHABLE_LINK_LABEL, withLayout,
} = await vite.ssrLoadModule('/src/process/ai/draftModel.ts')
const { AiDraftAbortError, AiDraftError, requestAiDraft } = await vite.ssrLoadModule('/src/process/ai/api.ts')
const { createProcessStore } = await vite.ssrLoadModule('/src/process/store.ts')
const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
after(async () => { await vite.close() })

const node = (id, name, extra = {}) => ({
  id, type: 'task', name, x: 0, y: 0, owner: '', description: '', input: '', output: '', assetIds: [], ...extra,
})
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })

test('十一种错误码文案互不相同，损坏、扫描件和未识别流程可以区分', () => {
  const messages = Object.values(AI_DRAFT_ERROR_MESSAGES)
  assert.equal(messages.length, 11)
  assert.equal(new Set(messages).size, 11)
  assert.match(AI_DRAFT_ERROR_MESSAGES.parse_failed, /损坏|无法读取/)
  assert.match(AI_DRAFT_ERROR_MESSAGES.scanned_pdf, /扫描/)
  assert.match(AI_DRAFT_ERROR_MESSAGES.not_a_process, /没有识别到流程/)
  assert.match(AI_DRAFT_ERROR_MESSAGES.invalid_input, /文字|文件/)
  assert.match(AI_DRAFT_ERROR_MESSAGES.llm_not_configured, /未配置语言模型/)
  for (const message of messages) assert.doesNotMatch(message, /inputKind|matchAssets/)
  assert.notEqual(AI_DRAFT_ERROR_MESSAGES.parse_failed, AI_DRAFT_ERROR_MESSAGES.scanned_pdf)
  assert.notEqual(AI_DRAFT_ERROR_MESSAGES.scanned_pdf, AI_DRAFT_ERROR_MESSAGES.not_a_process)
  assert.notEqual(AI_DRAFT_ERROR_MESSAGES.parse_failed, AI_DRAFT_ERROR_MESSAGES.not_a_process)
  for (const [code, message] of Object.entries(AI_DRAFT_ERROR_MESSAGES)) {
    if (code === 'not_a_process' || code === 'invalid_input') continue
    assert.equal(messageForAiDraftCode(code, '服务端原文', 422), message)
  }
  assert.equal(messageForAiDraftCode('not_a_process', AI_DRAFT_ERROR_MESSAGES.not_a_process, 422), AI_DRAFT_ERROR_MESSAGES.not_a_process)
  assert.equal(messageForAiDraftCode('not_a_process', '', 422), AI_DRAFT_ERROR_MESSAGES.not_a_process)
  assert.equal(messageForAiDraftCode('not_a_process', '未识别到流程：这是一份菜单', 422), '未识别到流程')
  assert.equal(messageForAiDraftCode('invalid_input', '请选择业务描述、会议纪要或 SOP', 422), '请选择业务描述、会议纪要或 SOP')
  assert.equal(messageForAiDraftCode('invalid_input', 'inputKind 只能是 description、minutes 或 sop', 422), AI_DRAFT_ERROR_MESSAGES.invalid_input)
  assert.equal(messageForAiDraftCode('empty_text', 'matchAssets 只能是 true 或 false', 422), AI_DRAFT_ERROR_MESSAGES.invalid_input)
  assert.equal(messageForAiDraftCode('empty_text', '请只提交文字或文件中的一种', 422), AI_DRAFT_ERROR_MESSAGES.invalid_input)
  assert.equal(messageForAiDraftCode('llm_failed', '未配置语言模型，无法生成流程初稿', 502), AI_DRAFT_ERROR_MESSAGES.llm_not_configured)
  assert.equal(messageForAiDraftCode(undefined, '', 503), AI_DRAFT_ERROR_MESSAGES.llm_not_configured)
  assert.equal(messageForAiDraftCode(undefined, '请稍后', 500), '请稍后')
  assert.equal(messageForAiDraftCode(undefined, '', 413), AI_DRAFT_ERROR_MESSAGES.file_too_large)
  assert.doesNotMatch(messageForAiDraftCode('invalid_input', 'inputKind 只能是 description', 422), /inputKind/)
})

test('本地输入检查复用超限、格式和空文本的文案', () => {
  assert.equal(validateDraftInput({ mode: 'text', text: '   ', file: null }), AI_DRAFT_ERROR_MESSAGES.empty_text)
  assert.equal(validateDraftInput({ mode: 'text', text: '字'.repeat(20001), file: null }), AI_DRAFT_ERROR_MESSAGES.text_too_long)
  assert.equal(validateDraftInput({ mode: 'text', text: '提交申请后审核', file: null }), null)
  assert.equal(validateDraftInput({ mode: 'file', text: '', file: null }), '请上传 txt、md、docx 或 pdf 文件。')
  assert.equal(validateDraftInput({ mode: 'file', text: '', file: { name: 'scan.png', size: 10 } }), AI_DRAFT_ERROR_MESSAGES.unsupported_type)
  assert.equal(validateDraftInput({ mode: 'file', text: '', file: { name: 'sop.pdf', size: 10 * 1024 * 1024 + 1 } }), AI_DRAFT_ERROR_MESSAGES.file_too_large)
  assert.equal(validateDraftInput({ mode: 'file', text: '', file: { name: 'sop.pdf', size: 20 } }), null)
})

test('原文对照只高亮非空引用，当前片段单独标记', () => {
  const segments = highlightSource('开始提交申请，通过后执行。', ['', '通过后', '通过后执行'], '通过后')
  assert.equal(segments.map((item) => item.text).join(''), '开始提交申请，通过后执行。')
  assert.ok(segments.some((item) => item.text === '通过后执行' && item.hit))
  assert.equal(segments.filter((item) => item.text === '通过后' && item.active).length, 0)
  const active = highlightSource('通过后执行', ['通过后'], '通过后')
  assert.deepEqual(active[0], { text: '通过后', hit: true, active: true })
})

test('资产建议只合并勾选项，不改名称，也不把前缀写回去', () => {
  const draft = {
    name: '采购',
    description: '说明',
    nodes: [
      node('review', '审核'),
      node('full', '归档', { assetIds: Array.from({ length: 100 }, (_, index) => `asset-${index}`) }),
    ],
    edges: [],
  }
  const suggestions = [
    { nodeId: 'review', assetId: 'tbl:orders', assetName: '订单表', mention: '订单', confidence: 0.95, preselect: true },
    { nodeId: 'review', assetId: 'tbl:orders', assetName: '订单表', mention: '订单表', confidence: 0.5, preselect: false },
    { nodeId: 'review', assetId: 'sys:erp', assetName: 'ERP', mention: '系统', confidence: 0.4, preselect: false },
    { nodeId: 'missing', assetId: 'tbl:ghost', assetName: '不存在', mention: '幽灵', confidence: 0.9, preselect: true },
    { nodeId: 'full', assetId: 'tbl:extra', assetName: '超额', mention: '超额', confidence: 0.99, preselect: true },
  ]
  const selected = initialSelectedKeys(suggestions)
  selected.add(suggestionKey(suggestions[2]))
  const merged = applyAssetSuggestions(draft, suggestions, selected)
  assert.deepEqual(merged.nodes[0].assetIds, ['tbl:orders', 'sys:erp'])
  assert.equal(merged.nodes[0].name, '审核')
  assert.equal(merged.nodes[1].assetIds.length, 100)
  assert.equal(merged.nodes[1].name, '归档')
  assert.equal(merged.name, draft.name)
  assert.equal(merged.description, draft.description)
  const renamed = applyAssetSuggestions({ ...draft, nodes: [{ ...draft.nodes[0], name: '复核' }] }, suggestions, new Set())
  assert.equal(renamed.nodes[0].name, '复核')
  assert.deepEqual(renamed.nodes[0].assetIds, [])
  assert.equal(countPendingReview([{ name: '【待确认】审核' }, { name: '审核' }, { name: '【待确认】结束' }]), 2)
})

test('初稿坐标先交给现有布局，名称保持不变', () => {
  const draft = {
    name: '【待确认】采购',
    description: '',
    nodes: [node('start', '开始', { type: 'start' }), node('end', '结束', { type: 'end' })],
    edges: [{ id: 'e1', source: 'start', target: 'end', label: '', sourceHandle: 'out', targetHandle: 'in' }],
  }
  const laid = withLayout(draft)
  assert.equal(laid.name, '【待确认】采购')
  assert.equal(laid.nodes[0].name, '开始')
  assert.ok(laid.nodes.every((item) => Number.isFinite(item.x) && Number.isFinite(item.y)))
  assert.ok(laid.nodes.some((item) => item.x !== 0 || item.y !== 0))
  assert.deepEqual(draft.nodes.map((item) => item.x), [0, 0])
})

test('生成请求使用 multipart，文字和文件二选一，并可取消', async () => {
  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method, body: init.body, signal: init.signal })
    if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    return response({
      draft: { name: '采购', description: '', nodes: [], edges: [] },
      uncertainties: [], assetSuggestions: [], unmatchedAssets: [], sourceText: '原文',
      stats: { model: 'test', latencyMs: 10, llmCalls: 1, repaired: false },
    })
  }
  const file = new File(['# 纪要'], 'notes.md', { type: 'text/markdown' })
  await requestAiDraft({ file, name: '  ', matchAssets: false, inputKind: 'minutes' })
  const form = calls[0].body
  assert.equal(new URL(calls[0].url, 'http://test').pathname, '/api/process/ai/draft')
  assert.equal(calls[0].method, 'POST')
  assert.equal(form.get('file').name, 'notes.md')
  assert.equal(form.get('text'), null)
  assert.equal(form.get('name'), null)
  assert.equal(form.get('matchAssets'), 'false')
  assert.equal(form.get('inputKind'), 'minutes')
  calls.length = 0
  await requestAiDraft({ text: ' 提交申请 ', name: '采购', inputKind: 'description' })
  assert.equal(calls[0].body.get('text'), '提交申请')
  assert.equal(calls[0].body.get('matchAssets'), 'true')
  assert.equal(calls[0].body.get('name'), '采购')
  await assert.rejects(requestAiDraft({ text: '同时', file }), (error) => error instanceof AiDraftError && calls.length === 1)
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(requestAiDraft({ text: '取消' }, controller.signal), (error) => error instanceof AiDraftAbortError)
})

test('接口错误码映射成固定文案，未识别流程不展示 reason', async () => {
  for (const code of ['parse_failed', 'scanned_pdf']) {
    globalThis.fetch = async () => response({ detail: { code, message: 'raw server text' } }, 422)
    await assert.rejects(requestAiDraft({ text: '一段流程' }), (error) => {
      assert.equal(error.message, AI_DRAFT_ERROR_MESSAGES[code])
      assert.notEqual(error.message, 'raw server text')
      return true
    })
  }
  globalThis.fetch = async () => response({
    detail: {
      code: 'not_a_process',
      message: AI_DRAFT_ERROR_MESSAGES.not_a_process,
      reason: '这是一份菜单，不要展示',
    },
  }, 422)
  await assert.rejects(requestAiDraft({ text: '一段流程' }), (error) => {
    assert.equal(error.message, AI_DRAFT_ERROR_MESSAGES.not_a_process)
    assert.equal(error.message.includes('菜单'), false)
    return true
  })
  globalThis.fetch = async () => response({ detail: { code: 'llm_not_configured', message: '内部：缺少 key' } }, 503)
  await assert.rejects(requestAiDraft({ text: '一段流程' }), (error) => {
    assert.equal(error.message, AI_DRAFT_ERROR_MESSAGES.llm_not_configured)
    assert.equal(error.message.includes('key'), false)
    return true
  })
})

test('衔接不明的步骤单独标出从开始接上的边，不改初稿标签', () => {
  const nodes = [
    node('start', '开始', { type: 'start' }),
    node('buy', '执行采购'),
    node('orphan', '【待确认】归档'),
  ]
  const edges = [
    { id: 'main', source: 'start', target: 'buy', label: '', sourceHandle: 'out', targetHandle: 'in' },
    { id: 'bridge', source: 'start', target: 'orphan', label: '', sourceHandle: 'out', targetHandle: 'in' },
  ]
  const uncertainties = [
    { nodeId: 'buy', kind: 'owner', reason: '负责人不明', sourceQuote: '' },
    { nodeId: 'orphan', kind: 'unreachable', reason: '该步骤与主流程的衔接不明确', sourceQuote: '归档' },
  ]
  const unreachable = unreachableNodeIds(uncertainties)
  assert.deepEqual([...unreachable], ['orphan'])
  const bridged = startBridgeTargetIds(nodes, edges, unreachable)
  assert.deepEqual([...bridged], ['orphan'])
  assert.equal(previewEdgeLabel(edges[1].label, true), UNREACHABLE_LINK_LABEL)
  assert.equal(previewEdgeLabel('通过', true), '通过')
  assert.equal(edges[1].label, '')
  const ordered = orderUncertainties(uncertainties)
  assert.equal(ordered[0].kind, 'unreachable')
  assert.match(UNREACHABLE_EXPLANATION, /开始/)
  assert.match(UNREACHABLE_EXPLANATION, /不是并列分支/)
})

test('未保存初稿的编辑和撤销不写库，保存时名称原样提交', async () => {
  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method ?? 'GET'
    calls.push({ url: String(url), method, body: init.body ? JSON.parse(init.body) : undefined })
    if (method === 'POST' && new URL(String(url), 'http://test').pathname === '/api/diagrams') {
      const body = JSON.parse(init.body)
      return response({ ...body, id: 'created-1', revision: 'created-1:0', nodeCount: body.nodes.length, edgeCount: body.edges.length, updatedAt: '2026-10-13T00:00:00Z' })
    }
    throw new Error(`unexpected ${method} ${url}`)
  }
  const store = createProcessStore()
  assert.equal(store.getState().openUnsaved({
    name: '采购', description: '说明',
    nodes: [node('review', '【待确认】审核')], edges: [],
  }), true)
  assert.equal(calls.length, 0)
  assert.equal(await store.getState().edit((draft) => { draft.nodes[0].name = '复核' }), true)
  assert.equal(await store.getState().undo(), true)
  assert.equal(store.getState().current.nodes[0].name, '【待确认】审核')
  assert.equal(await store.getState().redo(), true)
  assert.equal(store.getState().current.nodes[0].name, '复核')
  store.getState().updateUnsavedMeta('采购申请', '范围')
  assert.equal(calls.length, 0)
  assert.equal(store.getState().busy, false)
  const created = await store.getState().create({
    name: store.getState().current.name,
    description: store.getState().current.description,
    nodes: store.getState().current.nodes,
    edges: store.getState().current.edges,
  })
  assert.equal(created.nodes[0].name, '复核')
  assert.equal(created.name, '采购申请')
  assert.equal(calls.filter((call) => call.method === 'POST').length, 1)
  assert.equal(calls.some((call) => call.method === 'PUT'), false)
  assert.equal(calls[0].body.nodes[0].name, '复核')
  assert.doesNotMatch(calls[0].body.nodes[0].name, /待确认/)
})

test('覆盖初稿带上刚读取的 revision，冲突时内存初稿还在', async () => {
  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method ?? 'GET'
    const parsed = new URL(String(url), 'http://test')
    calls.push({ path: parsed.pathname, method, body: init.body ? JSON.parse(init.body) : undefined })
    if (method === 'PUT' && calls.filter((call) => call.method === 'PUT').length === 1) {
      return response({ detail: '流程已在其他窗口更新，请重新打开后重试' }, 409)
    }
    const body = init.body ? JSON.parse(init.body) : undefined
    return response({
      id: 'diagram-a', name: body?.name ?? '旧流程', description: body?.description ?? '',
      revision: 'diagram-a:2', nodeCount: body?.nodes?.length ?? 1, edgeCount: 0, updatedAt: '2026-10-13T01:00:00Z',
      nodes: body?.nodes ?? [node('old', '旧步骤')], edges: body?.edges ?? [],
    })
  }
  const store = createProcessStore()
  store.getState().openUnsaved({
    name: '新初稿', description: '',
    nodes: [node('review', '复核')], edges: [],
  })
  await assert.rejects(store.getState().overwrite('diagram-a', {
    name: '新初稿', description: '', nodes: [node('review', '复核')], edges: [],
  }, 'diagram-a:1'), (error) => error.status === 409)
  assert.equal(store.getState().current.id, '')
  assert.equal(store.getState().current.nodes[0].name, '复核')
  assert.equal(store.getState().error, null)
  assert.equal(calls[0].body.expected_revision, 'diagram-a:1')
  assert.equal(calls[0].body.nodes[0].name, '复核')
  const saved = await store.getState().overwrite('diagram-a', {
    name: store.getState().current.name,
    description: store.getState().current.description,
    nodes: store.getState().current.nodes,
    edges: store.getState().current.edges,
  }, 'diagram-a:1')
  assert.equal(saved.id, 'diagram-a')
  assert.equal(saved.nodes[0].name, '复核')
  assert.equal(calls.at(-1).body.expected_revision, 'diagram-a:1')
})
