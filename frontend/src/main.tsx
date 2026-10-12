import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { createRoot } from 'react-dom/client'
import WorkspaceApp from './WorkspaceApp'
import '@xyflow/react/dist/style.css'

createRoot(document.getElementById('root')!).render(
  <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#2563eb', borderRadius: 6, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif' } }}>
    <AntdApp>
      <WorkspaceApp />
    </AntdApp>
  </ConfigProvider>,
)
