# 数据流梳理平台

把散落各处的数据流信息（系统间链路、业务环节数据、表级/字段级血缘）**自动采集汇总成一张图**，并在可视化画布上**人工修正维护**的本地 Web 应用。

## 功能

- **三种视图**（同一张图的投影，不是三套数据）：
  - 系统视图：系统 / 数据库 / 接口 / 文件 / 外部方之间的数据流转
  - 业务视图：业务环节及其承载系统
  - 字段视图：表级血缘（连线含字段映射）
- **自动采集（自动为主，人工修）**：
  - **Excel 台账导入**：下载模板 → 填写 → 上传一键成图
  - **SQL 血缘解析**：粘贴/提交 SQL（INSERT-SELECT、CREATE TABLE AS、UPDATE、MERGE），基于 sqlglot 解析表级 + 字段级血缘，支持 mysql/postgresql/oracle/hive/spark 等方言
  - **数据库只读采集**：直连 MySQL / PostgreSQL 读取库表元数据（只做 information_schema 级反射，不改数据）
- **人工修正**：画布拖拽建点、连线建流、属性面板编辑。人工改过的字段会记入保护名单，**重复采集不会覆盖**；源中消失的自动项打「已消失」标记而非删除。
- **血缘追溯**：右键任意节点「追溯上游 / 下游」，整图高亮链路（跨视图）。
- **导出**：PNG 图片、JSON 备份/恢复。
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
  tests/                  # pytest（26 个用例）
frontend/                 # Vite + React 18 + AntD 5 + React Flow
  src/components/         # 画布、节点面板、属性面板、导入对话框
  src/utils/layout.ts     # dagre 自动布局
```

## 测试

```bash
cd backend
.venv/Scripts/python -m pytest tests -q
```

## 已知边界（第一期）

- 数据库采集只产出资产节点（库/表），表间流转关系来自 SQL 解析或 Excel 台账。
- PostgreSQL 的 schema 表在「SQL 解析」与「数据库采集」之间暂不自动对齐（`schema.table` vs `db.schema.table`）。
- 多人协作、定时采集、代码仓库扫描、全局搜索规划在第二期。
