# 数据流梳理平台

把散落各处的数据流信息（系统间链路、业务环节数据、表级/字段级血缘）**自动采集汇总成一张图**，并在可视化画布上**人工修正维护**的本地 Web 应用。

## 功能

- **三种视图**（同一张图的投影，不是三套数据）：
  - 系统视图：系统 / 数据库 / 接口 / 文件 / 外部方之间的数据流转
  - 业务视图：业务环节及其承载系统
  - 字段视图：表级血缘（连线含字段映射）
- **自动采集（自动为主，人工修）**：
  - **Excel 台账导入**：下载模板 → 填写 → 上传一键成图
  - **SQL 血缘解析**：粘贴/提交 SQL，基于 sqlglot 解析 INSERT-SELECT、CREATE TABLE AS、UPDATE 的表级与字段级血缘，支持 CTE、JOIN、UNION 和 mysql/postgresql/oracle/hive/spark 等方言；MERGE 当前提供表级血缘
  - **数据库只读采集**：直连 MySQL / PostgreSQL 读取库表元数据（只做 information_schema 级反射，不改数据）
- **人工修正**：画布拖拽建点、连线建流、属性面板编辑。人工改过的字段会记入保护名单，**重复采集不会覆盖**；仅在所有来源均确认对象消失后，才打「已消失」标记。
- **撤销 / 重做**：手动创建、编辑、删除、布局均保存完整图的前后状态，撤销也写回数据库。切视图、选择和查询不进入编辑历史；导入、采集、恢复备份和加载演示数据实际改变图数据后才清空历史，失败或未应用的导入保留历史。检测到其他页面修改了数据时，旧撤销不会覆盖新数据。
- **血缘追溯**：右键任意节点「追溯上游 / 下游」，整图高亮链路（跨视图）。
- **导出**：PNG 图片、JSON 备份/恢复。备份恢复完整校验节点、边及端点后一次提交；失败时保留原图。合并恢复包含已有节点的位置、边的标签和端点。
- **演示数据**：首次启动自动灌入一套订单业务场景，装好即可体验。

## 启动

需要 Python 3.10+ 和 Node.js 18+。

```bash
# 后端（端口 8000）
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # macOS/Linux
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 前端（端口 5173，另开一个终端）
cd frontend
pnpm install    # 或 npm install
pnpm dev
```

浏览器打开 [http://localhost:5173](http://localhost:5173)。前端通过 Vite 代理把 `/api` 转发到 8000 端口。

数据存在 `backend/data/dataflow.db`（SQLite），删除该文件即可重置。

## 目录结构

```
backend/                  # FastAPI + SQLAlchemy + SQLite
  app/main.py             # 入口（首次启动自动灌演示数据）
  app/models.py           # 节点/边模型（含来源 provenance 与人工保护字段）
  app/api.py              # REST API
  app/services/
    merge.py              # 合并服务：自动更新 + 人工保护 + 消失标记
    sql_lineage.py        # sqlglot SQL 血缘解析
    excel_import.py       # Excel 模板与解析
    db_introspect.py      # MySQL/PG 只读元数据采集
    graph.py              # 三视图过滤 + 血缘追溯
    demo_seed.py          # 演示数据
  tests/                  # pytest，含失败导入、恢复事务、来源隔离和字段血缘回归
frontend/                 # Vite + React 18 + AntD 5 + React Flow
  src/components/         # 画布、节点面板、属性面板、导入对话框
  src/utils/layout.ts     # dagre 自动布局
```

## 测试

```bash
cd backend
.venv/Scripts/python -m pytest tests -q

# 前端状态 / 撤销 / 竞态回归与生产构建
cd ../frontend
npm test
npm run build
```

## 已知边界（第一期）

- 数据库采集只产出资产节点（库/表），表间流转关系来自 SQL 解析或 Excel 台账。
- SQL/Excel 的 `tbl:*` 是逻辑表；数据库采集的 `physical-tbl:*` 是物理表，按数据库类型、主机、端口、库名隔离，避免同名资产互相覆盖。物理表的 `attrs.logical_table` 记录逻辑表关联候选，当前不自动合并。
- 旧版已经采集的资产和来源记录会保留。重新采集生成明确的物理资产，不会自动删除或重命名旧节点。旧版多来源消失状态无法准确还原各来源是否有效，采用保守保留，后续由各来源的完整采集结果逐步确认。
- SQL/Excel 出现解析或行校验错误时只返回预览与错误，不写入图。数据库部分反射失败或达到采集限额时保留已有资产和读取失败的字段信息。
- 缺少表结构元数据时，`SELECT *` 只提供表级血缘并提示字段未知；歧义字段会报错，不猜测物理来源。PostgreSQL schema 的逻辑/物理资产关联仍需人工确认。
- 多人协作、定时采集、代码仓库扫描、全局搜索规划在第二期。
