import { Alert, Button, Spin } from 'antd'
import { useEffect, useState } from 'react'
import { processApi } from './api'
import type { AssetUsage } from './types'

export function AssetProcessLinks({ assetId, onOpenProcess }: { assetId: string; onOpenProcess: (id: string) => void }) {
  const [usages, setUsages] = useState<AssetUsage[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let ignore = false
    setLoading(true)
    setError(null)
    processApi.assetUsage(assetId).then((result) => { if (!ignore) setUsages(result) })
      .catch((e: Error) => { if (!ignore) setError(e.message) })
      .finally(() => { if (!ignore) setLoading(false) })
    return () => { ignore = true }
  }, [assetId, attempt])
  return <section className="asset-process-links" aria-label="关联流程">
    <h3>关联流程</h3>
    {loading ? <Spin size="small" /> : error ? <Alert type="warning" message="关联流程未能加载" description={error} action={<Button size="small" onClick={() => setAttempt((n) => n + 1)}>重试</Button>} /> : usages.length ? (
      <ul>{usages.map((usage) => <li key={usage.id}><Button type="link" onClick={() => onOpenProcess(usage.id)}>{usage.name}</Button><span>{usage.nodeIds.length} 个步骤</span></li>)}</ul>
    ) : <p>暂无流程引用此资产，可在流程步骤中添加关联。</p>}
  </section>
}
