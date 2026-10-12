import { ReactFlowProvider } from '@xyflow/react'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { createRoot } from 'react-dom/client'
import App from './App'
import '@xyflow/react/dist/style.css'

createRoot(document.getElementById('root')!).render(
  <ConfigProvider locale={zhCN}>
    <AntdApp>
      <ReactFlowProvider>
        <App />
      </ReactFlowProvider>
    </AntdApp>
  </ConfigProvider>,
)
