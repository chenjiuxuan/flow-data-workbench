import { InboxOutlined } from '@ant-design/icons'
import { Alert, App as AntdApp, Button, Input, Segmented, Switch, Upload } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ProcessCreate } from '../types'
import { AiDraftAbortError, requestAiDraft } from './api'
import { DraftPreview } from './DraftPreview'
import { applyAssetSuggestions, initialSelectedKeys, unreachableNodeIds, withLayout } from './draftModel'
import { validateDraftInput } from './messages'
import { SuggestionSidebar } from './SuggestionSidebar'
import type { AiDraftInputKind, AiDraftResponse } from './types'
import './ai-draft.css'

export interface AiDraftActivity { busy: boolean; hasDraft: boolean; hasInput: boolean }

interface Props {
  initialName: string
  initialDescription: string
  onEnterEditor: (draft: ProcessCreate) => void
  onActivity: (activity: AiDraftActivity) => void
}

const INPUT_KINDS: { label: string; value: AiDraftInputKind }[] = [
  { label: '文字描述', value: 'description' },
  { label: '会议纪要', value: 'minutes' },
  { label: 'SOP 文档', value: 'sop' },
]

export function AiDraftPanel({ initialName, initialDescription, onEnterEditor, onActivity }: Props) {
  const { message } = AntdApp.useApp()
  const [mode, setMode] = useState<'text' | 'file'>('text')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState(initialName)
  const [matchAssets, setMatchAssets] = useState(true)
  const [inputKind, setInputKind] = useState<AiDraftInputKind>('description')
  const [phase, setPhase] = useState<'input' | 'generating' | 'preview'>('input')
  const [result, setResult] = useState<AiDraftResponse | null>(null)
  const [positioned, setPositioned] = useState<ProcessCreate | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null)
  const [stage, setStage] = useState(0)
  const abortRef = useRef<AbortController | null>(null)
  const requestSeq = useRef(0)
  const resultRef = useRef(result)
  const mounted = useRef(true)
  resultRef.current = result

  const stages = matchAssets
    ? ['正在读取内容', '正在识别步骤与分支', '正在整理成流程图', '正在匹配已有资产']
    : ['正在读取内容', '正在识别步骤与分支', '正在整理成流程图']
  const stageCount = stages.length

  useEffect(() => {
    onActivity({
      busy: phase === 'generating',
      hasDraft: result !== null,
      hasInput: mode === 'file' ? file !== null : text.trim() !== '',
    })
  }, [file, mode, onActivity, phase, result, text])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      abortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    if (phase !== 'generating') return
    setStage(0)
    const timers = Array.from({ length: stageCount }, (_, index) => window.setTimeout(() => setStage(index), index * 7000))
    return () => timers.forEach((timer) => window.clearTimeout(timer))
  }, [phase, stageCount])

  const generate = async () => {
    const problem = validateDraftInput({ mode, text, file })
    if (problem) { setError(problem); return }
    const seq = ++requestSeq.current
    const controller = new AbortController()
    abortRef.current?.abort()
    abortRef.current = controller
    setError(null)
    setStage(0)
    setPhase('generating')
    const stale = () => !mounted.current || requestSeq.current !== seq
    try {
      const response = await requestAiDraft({
        text: mode === 'text' ? text : undefined,
        file: mode === 'file' ? file ?? undefined : undefined,
        name,
        matchAssets,
        inputKind,
      }, controller.signal)
      if (stale()) return
      if (controller.signal.aborted) {
        setPhase(resultRef.current ? 'preview' : 'input')
        return
      }
      let laidOut: ProcessCreate
      try { laidOut = withLayout(response.draft) }
      catch (layoutError) {
        setError(`整理布局失败：${(layoutError as Error).message}`)
        setPhase(resultRef.current ? 'preview' : 'input')
        return
      }
      setResult(response)
      setPositioned(laidOut)
      setSelected(initialSelectedKeys(response.assetSuggestions))
      setFocusNodeId(null)
      setPhase('preview')
    } catch (caught) {
      if (stale()) return
      if (caught instanceof AiDraftAbortError || controller.signal.aborted) {
        setPhase(resultRef.current ? 'preview' : 'input')
        return
      }
      setError(caught instanceof Error ? caught.message : '生成失败，请稍后重试。')
      setPhase(resultRef.current ? 'preview' : 'input')
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }

  const enterEditor = () => {
    if (!positioned || !result) return
    const merged = applyAssetSuggestions(positioned, result.assetSuggestions, selected)
    const nextName = name.trim() || merged.name.trim()
    if (!nextName) {
      message.warning('请填写流程名称，或重新生成让 AI 建议一个名称')
      return
    }
    onEnterEditor({
      ...merged,
      name: nextName,
      description: initialDescription.trim() || merged.description,
    })
  }

  const suggestedName = positioned?.name.trim() || result?.draft.name.trim() || ''

  return (
    <div className="ai-draft">
      {phase === 'preview' && positioned && result ? (
        <>
          <div className="ai-draft__toolbar">
            <div>
              <h2>初稿预览</h2>
              <p>{result.stats.model} · {(result.stats.latencyMs / 1000).toFixed(1)} 秒{result.stats.repaired ? ' · 已自动修正格式' : ''} · 尚未写入数据库</p>
            </div>
            <div className="ai-draft__actions">
              <Input className="ai-draft-name" value={name} maxLength={200} placeholder={suggestedName || '流程名称'} aria-label="流程名称" onChange={(event) => setName(event.target.value)} />
              <Button onClick={() => setPhase('input')}>修改输入</Button>
              <Button onClick={() => void generate()}>重新生成</Button>
              <Button type="primary" onClick={enterEditor}>进入编辑</Button>
            </div>
          </div>
          {error && <Alert type="error" showIcon banner message={error} />}
          <div className="ai-draft__stage">
            <DraftPreview draft={positioned} unreachableNodeIds={unreachableNodeIds(result.uncertainties)} focusNodeId={focusNodeId} onFocusNode={setFocusNodeId} />
            <SuggestionSidebar
              draft={positioned}
              uncertainties={result.uncertainties}
              assetSuggestions={result.assetSuggestions}
              unmatchedAssets={result.unmatchedAssets}
              sourceText={result.sourceText}
              selectedKeys={selected}
              focusNodeId={focusNodeId}
              onToggle={(key, checked) => setSelected((previous) => {
                const next = new Set(previous)
                if (checked) next.add(key)
                else next.delete(key)
                return next
              })}
              onFocusNode={setFocusNodeId}
            />
          </div>
        </>
      ) : (
        <div className="ai-draft-card">
          {phase === 'generating' ? (
            <>
              <h2>正在生成初稿</h2>
              <p>这一步只读取内容并整理流程图，不会创建或修改已有流程。</p>
              <ol className="ai-draft-progress" aria-live="polite">
                {stages.map((label, index) => (
                  <li key={label} className={index < stage ? 'is-done' : index === stage ? 'is-current' : undefined}>{label}</li>
                ))}
              </ol>
              <div className="ai-draft-card__footer">
                <Button onClick={() => abortRef.current?.abort()}>取消</Button>
              </div>
            </>
          ) : (
            <>
              <h2>AI 生成初稿</h2>
              <p>粘贴一段描述、纪要或 SOP，或上传 txt、md、docx、pdf。扫描版 PDF 无法识别。</p>
              {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
              <div className="ai-draft-fields">
                <Segmented
                  value={mode}
                  options={[{ label: '粘贴文字', value: 'text' }, { label: '上传文件', value: 'file' }]}
                  onChange={(value) => setMode(value as 'text' | 'file')}
                />
                {mode === 'text' ? (
                  <div className="ai-draft-field">
                    <span>流程内容</span>
                    <Input.TextArea
                      value={text}
                      rows={10}
                      maxLength={20000}
                      showCount
                      placeholder="例如：员工提交采购申请，部门负责人审核。通过后由采购执行并结束；不通过则退回修改。"
                      onChange={(event) => setText(event.target.value)}
                    />
                  </div>
                ) : (
                  <Upload.Dragger
                    accept=".txt,.md,.docx,.pdf"
                    maxCount={1}
                    fileList={file ? [{ uid: file.name, name: file.name, size: file.size, status: 'done' }] : []}
                    beforeUpload={(next) => {
                      const problem = validateDraftInput({ mode: 'file', text: '', file: next })
                      if (problem) { setError(problem); return Upload.LIST_IGNORE }
                      setError(null)
                      setFile(next)
                      return Upload.LIST_IGNORE
                    }}
                    onRemove={() => { setFile(null); return true }}
                  >
                    <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                    <p className="ant-upload-text">点击或拖入 txt、md、docx、pdf</p>
                    <p className="ant-upload-hint">最大 10 MiB。pdf 需要有可复制的文字层。</p>
                  </Upload.Dragger>
                )}
                <div className="ai-draft-field">
                  <span>流程名称</span>
                  <Input value={name} maxLength={200} placeholder="留空则由 AI 建议" onChange={(event) => setName(event.target.value)} />
                </div>
                <div className="ai-draft-inline">
                  <span>输入类型</span>
                  <Segmented value={inputKind} options={INPUT_KINDS} onChange={(value) => setInputKind(value as AiDraftInputKind)} />
                </div>
                <div className="ai-draft-inline">
                  <span>匹配已有资产</span>
                  <Switch checked={matchAssets} onChange={setMatchAssets} />
                </div>
                <div className="ai-draft-card__footer">
                  {result && <Button onClick={() => { setError(null); setPhase('preview') }}>返回预览</Button>}
                  <Button type="primary" onClick={() => void generate()}>生成初稿</Button>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
