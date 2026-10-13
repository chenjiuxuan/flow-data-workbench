import type { ProcessBackup, ProcessCreate } from './types'

const fileName = (name: string) => name.replace(/[<>:"/\\|?*\x00-\x1F]/g, '_').slice(0, 100) || '流程'

export function downloadProcessJson(data: ProcessCreate) {
  const backup: ProcessBackup = {
    format: 'process-diagram/v1',
    name: data.name,
    description: data.description,
    nodes: data.nodes,
    edges: data.edges,
  }
  const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `${fileName(data.name)}.json`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
