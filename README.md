# Python版本 - 多Agent智能合同审查系统

基于 LangGraph 的多Agent智能合同审查系统 Python 实现（三语言参考实现，对齐状态见 v2.0.1）。

## 技术栈

- **LangGraph** — Agent 编排（含 interrupt / resume）
- **FastAPI** — 异步 Web API
- **Pydantic / SQLAlchemy** — 模型与持久化
- **FAISS** — 向量检索 + RAG（可选）
- **Celery + Redis** — 异步任务（可选）
- **多 LLM** — qwen / minimax / openai / auto（OpenAI 兼容接口）

## 项目结构

```
python/
├── src/
│   ├── agents/                    # 四个Agent实现
│   │   ├── clause_extraction.py   # Agent 1: 条款提取
│   │   ├── risk_identification.py # Agent 2: 风险识别（规则 + LLM 双引擎）
│   │   ├── compliance_check.py    # Agent 3: 合规检查
│   │   └── suggestion.py          # Agent 4: 修改建议
│   ├── pipeline/
│   │   ├── graph.py               # LangGraph 编排 + 分步进度回调
│   │   └── state.py               # 共享状态（含 current_step / step_log / progress）
│   ├── api/
│   │   ├── main.py                # FastAPI 入口（lifespan + 迁移 + 种子）
│   │   ├── routers/               # 9 个路由模块
│   │   ├── schemas/               # Pydantic 请求/响应模型
│   │   ├── auth/                  # JWT + RBAC
│   │   ├── dependencies/          # 依赖注入
│   │   ├── middleware.py          # 中间件
│   │   ├── rate_limit.py          # 限流
│   │   └── exceptions.py          # 统一异常处理
│   ├── services/                  # 业务逻辑层（ReviewService / StorageService 等）
│   ├── db/                        # SQLAlchemy async 模型 + CRUD
│   ├── rules/                     # 规则引擎 + YAML 定义（支持热加载）
│   ├── nlp/legal_nlp.py           # 法律 NLP 工具
│   ├── parsers/document_parser.py # PDF/DOCX 解析
│   ├── vector/                    # FAISS 向量存储 + RAG
│   ├── worker/                    # Celery 异步任务（可选）
│   ├── config.py                  # 多 LLM 提供商配置
│   ├── tracing.py                 # OpenTelemetry 链路追踪
│   ├── cache.py / resilience.py / logging_config.py
│   └── ...
├── tests/                         # 约 17 个测试模块（pytest 收集约 150+ 用例：auth/db/e2e/rag/report/rules/…）
├── migrations/                    # 启动时自动执行的 SQL 迁移
├── data/seed.sql                  # 默认账号种子（admin/admin123）
├── requirements.txt           # 运行时依赖（版本锁定）
├── requirements-dev.txt       # 开发/测试依赖（pytest 等）
├── pytest.ini / conftest.py   # 本地测试配置
├── .env.example
├── docker-compose.yml         # 本地运行的依赖服务（postgres + redis，不含后端）
└── scripts/                   # 建索引 / 灌数据脚本
```

## 快速开始（本地）

> 前置条件：Python 3.11+（推荐 3.12）、可选 Docker（仅用于起 PostgreSQL / Redis）。

### 1. 创建虚拟环境

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

python -V   # 确认 >= 3.11
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
# 需要跑测试时再装开发依赖
pip install -r requirements-dev.txt
```

### 3. 启动依赖服务（PostgreSQL + Redis）

项目只需要 PostgreSQL（必需）和 Redis（仅 Celery 异步任务用到）。用 Docker 起最省事：

```bash
docker compose up -d            #  postgres 5433 / redis 6379，端口已与 .env 对齐
docker compose ps
```

不想用 Docker 也可以本地安装：PostgreSQL 监听 `5433`、Redis 监听 `6379`，并创建数据库
`contract_review`（用户/密码 `postgres`）。**连不上数据库也能启动**，此时会自动降级为内存存储模式
（日志出现「数据库连接失败，使用内存存储模式」），但重启后数据会丢失。

### 4. 配置环境变量

```bash
cp .env.example .env            # Windows 用 copy .env.example .env
# 编辑 .env，填入你的 API Key
```

`.env` 里几个本地运行相关的关键项：

```bash
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/contract_review
CELERY_BROKER_URL=redis://localhost:6379/0
SEED_ON_STARTUP=true            # 首次启动自动写入 admin/admin123
CONTRACT_LLM_API_KEY=sk-xxx     # 至少一个 provider 的 Key
```

### 5. 启动服务

在**项目根目录**（`python/`）下执行，`src` 才能被正确导入：

```bash
# 带热重载（推荐开发用）
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# 或
python -m src.api.main
```

### 6. 访问API

- Swagger文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/api/v1/health
- 默认账号：`admin / admin123`（`SEED_ON_STARTUP=true` 时首次启动自动注入）

### 常用命令

```bash
docker compose down              # 停止依赖服务（数据卷保留）
docker compose down -v           # 停止并清空数据卷（重置数据库）
docker compose exec postgres psql -U postgres -d contract_review
```

LLM Key 通过 `.env` 配置，例如：

```bash
# code/python/.env
LLM_PROVIDER=auto
CONTRACT_LLM_API_KEY=your_key_here
CONTRACT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CONTRACT_LLM_MODEL=qwen-max
# 或 MINIMAX_API_KEY / OPENAI_API_KEY，由 LLM_PROVIDER=auto 自动选择
```

可选组件（本地）：

```bash
# 异步任务 worker（需 Redis）
celery -A src.worker.app:celery_app worker --loglevel=info --concurrency=2
```

### 前端界面

前端是独立项目（`../frontend`），后端不托管静态页面，需要单独启动。

**方式 A：本地 Node 运行（推荐，改代码即时生效）**
 # http://localhost:5173
```bash
cd ./frontend
npm install
npm run dev           
```

**方式 B：Docker 运行（需后端已在宿主机 8000 端口运行）**
# http://localhost:5173
```bash
cd ../python
docker compose --profile frontend up -d     
docker compose logs -f frontend
```

容器内 Vite 会把 `/api` 代理到 `VITE_API_TARGET`（默认已设为 `http://host.docker.internal:8000`，
即宿主机上的本地后端）；后端端口不同可改 compose 里该变量，或在 `../frontend/.env` 中覆盖。

### 可选依赖说明

`faiss-cpu` / `sentence-transformers` 只用于 RAG（`vector/`），代码里是懒加载 + `try/except`，
装不上也不影响主流程启动。安装慢时可用国内镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## API接口

登录取到 Token 后，其余接口均需在请求头带 `Authorization: Bearer <token>`。

### 登录

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

### 合同审查（同步模式，等待流水线完成）

```bash
curl -X POST 'http://localhost:8000/api/v1/review?mode=sync' \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "review_body": {
      "text": "甲方：北京科技有限公司\n乙方：上海贸易有限公司\n第一条 付款：买方须在交货后30日内付款，逾期须支付无限违约金。",
      "with_human_review": false
    }
  }'
```

### 合同审查（异步模式，前端分步进度轮询）

```bash
# 立即返回 review_id，后台流水线在 asyncio 任务中执行
curl -X POST 'http://localhost:8000/api/v1/review?mode=async' \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{ "review_body": { "text": "...", "with_human_review": false } }'

# 1s 轮询查看进度（current_step / step_log / progress）
curl http://localhost:8000/api/v1/review/{review_id} \
  -H "Authorization: Bearer $TOKEN"
```

### 健康检查

```bash
curl http://localhost:8000/api/v1/health
```

完整端点见 Swagger：<http://localhost:8000/docs>（约 42 个端点，覆盖认证 / 审查 / 规则 / 模板 / 法规 / 协商 / 工具；与 Java/Go 对齐见 `FEATURE_ALIGNMENT_RECORD.md`）。

## 核心概念

### 共享状态

所有 Agent 通过 `ContractReviewState` 共享数据（Pydantic 模型），并追加分步进度字段供前端轮询：

```python
class ContractReviewState(BaseModel):
    review_id: str
    status: ReviewStatus
    raw_text: str
    clauses: list[Clause]
    risk_findings: list[RiskFinding]
    compliance_findings: list[ComplianceFinding]
    suggestions: list[Suggestion]
    needs_human_review: bool

    # 分步进度追踪
    current_step: str = ""              # clause_extraction / risk_identification / ...
    step_log: list[StepEvent] = []      # [{step, status, started_at, finished_at, message}]
    progress: float = 0.0               # 0.0 – 1.0
```

### Agent编排

使用LangGraph StateGraph编排四个Agent：

```python
graph = StateGraph(ContractReviewState)
graph.add_node("clause_extraction", clause_agent)
graph.add_node("risk_identification", risk_agent)
graph.add_node("compliance_check", compliance_agent)
graph.add_node("suggestion", suggestion_agent)
```

### 条件路由

根据风险等级决定是否需要人工审核：

```python
def should_human_review(state):
    if state.needs_human_review:
        return "human_review"
    return "complete"
```

## 测试

```bash
# 首次运行前安装测试依赖
pip install -r requirements-dev.txt

# 在项目根目录运行全部测试
python -m pytest

# 运行测试并生成覆盖率报告
python -m pytest --cov=src --cov-report=html
```

> `pytest.ini` 已固定 `testpaths=tests`、异步模式 `strict`；根 `conftest.py` 负责把项目根目录
> 加入 `sys.path`，保证 `from src...` 导入可用。用例默认使用 SQLite 内存库（`aiosqlite`），无需 PostgreSQL。

## 常见问题

### Q: 如何添加新的Agent？

A: 三步走：
1. 创建新的Agent类，实现`__call__`方法
2. 在`state.py`中添加新的输出字段
3. 在`graph.py`中注册节点和边

### Q: 如何添加新的规则？

A: 编辑 `src/rules/definitions/*.yaml`（支持规则热加载），或通过 `/api/v1/rules` 路由动态 CRUD 管理。

### Q: 如何切换大模型？

A: 编辑 `.env`：
- `LLM_PROVIDER=auto` 自动检测已配置的 Key（qwen / minimax / openai）
- `LLM_PROVIDER=qwen|minimax|openai` 强制指定
- 任何 OpenAI 兼容端点都可通过 `CONTRACT_LLM_BASE_URL` 接入

### Q: 为什么要单进程运行？

A: 异步模式下的分步进度靠 `asyncio.create_task` 后台运行，多 worker（`uvicorn --workers > 1`）会导致后台任务丢失。
本地开发用 `python -m uvicorn src.api.main:app --reload` 即可；生产建议单进程 + 多实例横向扩展，
或用 Celery worker 分担负载。
