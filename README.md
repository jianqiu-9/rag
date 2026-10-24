# Agent Notes · Agent 面经知识库

本地单用户 RAG 应用。导入 UTF-8 Markdown/TXT，用自然语言查找面试题、参考答案和可定位的原文来源。没有证据时明确提示资料不足。

前端使用 **React + TypeScript**，后端使用 **FastAPI**；本地 **EmbeddingGemma-300M** 生成 768 维向量，**PostgreSQL + pgvector** 保存文档、向量及元数据，远程 **DeepSeek Flash** 判断证据并组织答案。

## 项目功能

- 上传和管理本地 Markdown/TXT 面经，后台异步解析、切分与向量入库。
- 按技术主题列题，或解释具体问题；回答附带文件、章节及原文行号。
- 相同内容去重，同名文件安全替换；新版本失败时旧版本继续可用。
- 无证据时提示资料不足，原文没有答案时不自动补写。
- 支持删除、失败重试和重启恢复；按请求保存问答、实际模型输入/输出、证据快照和错误步骤，默认保留 30 天。

当前为单用户 MVP，不包含登录、多轮对话、互联网搜索，以及 PDF/DOCX 导入。

## 环境要求

| 项目 | 要求或说明 |
| --- | --- |
| Python | 推荐 **3.12**；后端声明支持 `>=3.12,<3.14`，不要使用本机原有的 Python 3.14 环境 |
| Python 依赖管理 | uv；后端隔离环境位于 `backend/.venv` |
| Node.js / npm | 建议 Node.js 22 LTS 或更新的受支持版本，使用 npm 安装前端依赖 |
| 数据库 | PostgreSQL + pgvector；可选 Docker Compose，或下文的本机 pgserver 启动方式 |
| 本地模型 | `google/embeddinggemma-300m`，需要先完成许可/访问授权和权重下载 |
| 远程模型 | DeepSeek Flash，需要有效的 `DEEPSEEK_API_KEY` 与网络连接 |
| 硬件 | 本机 Apple M1 / 8GB，默认 CPU 推理、批大小 4；实际召回速度仍待真实模型测量 |
| 浏览器 | 使用支持现代 JavaScript 的浏览器；自动化浏览器及文档图测试使用 Google Chrome |

安装后的版本由 `backend/uv.lock` 和 `frontend/package-lock.json` 锁定。模型运行框架与权重需预留数 GB 磁盘空间。

**本机当前状态（2026-10-23）：** 原生 PostgreSQL + pgvector、EmbeddingGemma 和 DeepSeek 均已就绪，真实应用已启动；访问 [项目页面](http://127.0.0.1:8000/)。模型权重已完成 SHA256 校验并锁定版本，离线中文向量化输出 768 维。已使用合成样例通过上传、向量入库、远程问答和原文引用的真实链路验证，临时样例已清理。完整固定验收集及性能指标仍待验证。此状态是记录，不保证进程在电脑重启后仍运行。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [PRD](doc/agent_interview_rag_mvp_prd.md) | 产品约束与验收标准 |
| [架构文档](doc/architecture.md) | 模块职责、数据模型、事务边界、扩展方式 |
| [时序图](doc/sequence-diagrams.md) | 上传、替换、问答、重试、删除的 Mermaid 时序图 |
| [API 说明](doc/api.md) | HTTP 契约、请求示例和错误处理 |
| [验收说明](doc/acceptance.md) | 固定样本、标定流程、通过标准及覆盖范围 |
| [验证记录](doc/verification.md) | 本次实际执行的验证与尚未完成的真实模型验收 |

## 首次运行

以下命令从项目根目录执行：

```bash
cd /Users/Admin/PycharmProjects/Rag
```

本节使用 Docker Compose 管理数据库；本机没有 Docker 时使用后文“无 Docker 启动”。Docker 只运行数据库，Python 模型在宿主机运行。首次安装/下载需要联网；日常生成答案也需要连接 DeepSeek。

### 1. 安装依赖并配置环境

```bash
test -f .env || cp .env.example .env
uv sync --project backend --python 3.12 --extra dev --extra models
npm --prefix frontend ci
```

在 `.env` 设置 `DEEPSEEK_API_KEY`。密钥只由后端读取，不使用 `VITE_` 前缀，不放到前端代码中。`DATABASE_URL` 的密码需要与 `POSTGRES_PASSWORD` 一致。

默认数据库口令仅用于监听本机的开发环境。如果 5432 端口已经被使用，调整 `compose.yaml` 的映射及 `DATABASE_URL`。

常用配置如下，完整示例见 `.env.example`：

| 配置项 | 默认值 / 作用 |
| --- | --- |
| `DATABASE_URL` | `postgresql://rag:rag_local@127.0.0.1:5432/rag`，Docker 或自行管理的数据库连接 |
| `DEEPSEEK_API_KEY` | 必填；远程证据判断及答案生成所需密钥 |
| `DEEPSEEK_MODEL` | `deepseek-flash` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DATA_DIR` | `./data`；相对项目根目录，保存原文件和日志 |
| `MODEL_CACHE_DIR` | `./data/models`；本地模型缓存及 SHA 锁文件 |
| `EMBEDDING_REVISION` | 默认留空，读取下载命令生成的锁文件；显式设置时必须是 40 位 SHA |
| `EMBEDDING_BATCH_SIZE` | `4`；内存紧张可降到 `1` |
| `SIMILARITY_THRESHOLD` | `0.5`；仅为调试起点，需要用固定样本标定 |
| `TOP_K` | `8`；普通问答候选片段数量 |
| `LLM_TIMEOUT_SECONDS` | `30`；一次生成调用与重试共用的总预算 |

`.env` 从项目根目录读取。已有配置不会被上面的复制命令覆盖；修改配置后需要重新启动后端。`HF_TOKEN` 用于首次模型下载，作为下载进程环境变量提供；它与 DeepSeek Key 是两个不同的凭据。

### 2. 启动数据库并执行迁移

```bash
docker compose up -d postgres
docker compose ps
backend/.venv/bin/rag migrate
```

等待数据库健康后运行迁移。迁移记录保存在 `schema_migrations`，重复运行不会重复建表。不要编辑已经执行过的迁移；变更结构时增加新的编号 SQL。

### 3. 下载并锁定本地模型

先访问 [EmbeddingGemma 模型页面](https://huggingface.co/google/embeddinggemma-300m)，按提供方要求接受模型使用条款并取得访问权限。若下载需要身份认证，将 Hugging Face 令牌以 `HF_TOKEN` 环境变量提供给下载进程，不提交到仓库。

```bash
backend/.venv/bin/rag download-model
```

命令把 `main` 解析为不可变的提交 SHA，并保存 `data/models/model-lock.json`。实际下载与运行都使用该 SHA；后续查询不会联网解析 `main` 或隐式下载权重。也可通过 `--revision <SHA>` 下载指定版本，或在 `.env` 设置 `EMBEDDING_REVISION`。

Apple M1 / 8GB 默认使用 CPU、float32、批大小 4。CPU 方案兼容性优先；模型、运行框架及其他应用的总内存占用高于模型权重本身。批大小可减到 1。第一次加载模型可能较慢。

### 4. 启动开发服务

终端一：

```bash
backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

终端二：

```bash
npm --prefix frontend run dev
```

打开 [本地页面](http://127.0.0.1:5173)。前端通过 Vite 代理访问后端 `/api`。健康检查：[health](http://127.0.0.1:8000/api/health)，交互 API：[Swagger](http://127.0.0.1:8000/docs)。

PyCharm 也可以运行根目录 `main.py`，它会调用 `backend/.venv` 启动后端，避免误用原先示例项目的解释器。默认用一个 Uvicorn 进程；入库 worker 通过数据库会话锁保持单实例。

### 5. 构建并同源运行

```bash
npm --prefix frontend run build
backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

构建完成后重新启动后端，FastAPI 自动托管 `frontend/dist`。此时直接打开 [应用](http://127.0.0.1:8000)，无需 Vite。

## 无 Docker 启动（本机已验证）

本机没有 Docker 时，可使用可选依赖 pgserver 提供的真实 PostgreSQL/pgvector。它不是假数据库，也不使用测试替身；应用依旧连接真实 EmbeddingGemma 和 DeepSeek 适配器。pgserver 依赖平台 wheel，下面方式已在本机 macOS Apple Silicon / Python 3.12 验证。

先完成上文环境配置及模型下载，并安装包含 pgserver 的依赖、构建页面：

```bash
uv sync --project backend --python 3.12 --extra dev --extra models --extra postgres-test
npm --prefix frontend ci
npm --prefix frontend run build
```

接着在项目根目录运行以下命令，并保持终端开启。如果 8000 端口已有项目服务，不要重复启动：

```bash
LC_ALL=C backend/.venv/bin/python scripts/run_local.py
```

打开 [项目页面](http://127.0.0.1:8000/)。该入口使用现有 `data/postgres` 数据库，确保 PostgreSQL 仅监听 `127.0.0.1:5433`，执行幂等迁移后启动应用。不需要另外运行 `rag migrate` 或根目录 `main.py`。`LC_ALL=C` 用于避免本机 locale 影响数据库初始化。可通过 `--db-port`、`--port`、`--pgdata` 显式调整本地端口及数据库目录。

该方式的数据库目录是 `data/postgres`，停止进程后保留数据，下次执行同一命令继续使用；它与 Docker 命名卷是两套独立数据库，切换启动方式不会自动迁移知识库。`cleanup_mode="stop"` 表示退出时停止数据库进程，不表示删除数据。

## 使用 DBeaver 查看本机数据库

本机原生模式已开启 TCP，保持本地启动入口运行，在 DBeaver 新建 **PostgreSQL** 连接：

| 参数 | 当前值 |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `5433` |
| Database | `postgres` |
| Username | `postgres` |
| Password | 留空，当前本机开发实例沿用已有 trust 认证 |
| SSL | 关闭 |
| JDBC URL | `jdbc:postgresql://127.0.0.1:5433/postgres` |

点击“测试连接”，成功后展开 `postgres → Schemas → public → Tables`。`documents` 查看入库状态，`chunks` 查看文本与向量，`query_logs` 查看问答日志。

此端口只绑定本机回环地址，不提供局域网访问。原生入口会在必要时调整数据库启动参数；只修改 postgresql.conf 不能覆盖 pgserver 默认的 `-h ""` 参数。以后使用 `scripts/run_local.py` 启动，避免回到仅 Socket 模式。

本机 `.env` 的 `DATABASE_URL` 已设为 `postgresql://postgres@127.0.0.1:5433/postgres`；`.env.example` 仍保留 Docker 示例。切换 Docker 模式时需相应修改连接，两个模式的数据库及数据目录不共用。

## 日常启动、访问与停止

| 模式 | 启动方式 | 访问地址 |
| --- | --- | --- |
| Docker 数据库 + 前后端开发服务 | `docker compose up -d postgres`，随后分别启动 Uvicorn 与 Vite | `http://127.0.0.1:5173` |
| Docker 数据库 + 已构建页面 | 启动数据库，再运行 Uvicorn；页面修改后重新 build | `http://127.0.0.1:8000` |
| 无 Docker 本地模式 | 运行 `scripts/run_local.py` | `http://127.0.0.1:8000` |

首次设置完成后，无需每次重复安装依赖或下载模型。关闭服务使用启动终端中的 `Ctrl+C`；开发模式需分别关闭 Vite 和后端。Docker 模式另外运行 `docker compose stop`。无 Docker 模式的数据库随上述启动进程退出而停止。

可用以下命令检查后端和数据库是否就绪：

```bash
curl --fail http://127.0.0.1:8000/api/health
```

健康接口检查数据库，不发付费模型请求；`model_cached=true` 仅表示模型锁文件存在，不等于 DeepSeek Key 有效或所有权重均已验证。

## 使用流程与业务规则

1. 在知识库页选择或拖入 MD/TXT，每份默认不超过 10 MB，每批最多 20 份。
2. 上传成功立即获得版本 ID；后台解析、切分、生成向量，前端每 2 秒轮询。
3. 同一内容哈希不重复导入；同名不同内容先处理新版本，成功后切换，失败保留旧版本。
4. 在检索问答页输入问题。可从问题文本识别列题数量，也可填写显式数量（1–20），显式值优先。
5. 查看答案引用，展开原文件并高亮原始行号。没有原文答案的题目只显示原题及提示。
6. 删除文档会删除所有版本、文本块、向量及文件副本。处理中任务不能让它重新出现。

**知识文件放在哪里：** 原始文件可以放在电脑任意文件夹，通过页面上传即可。系统自动把副本保存到 `data/uploads`；不要直接把文件复制进该目录，复制不会触发解析和向量入库。演示时可上传 `fixtures/acceptance/v1` 中的 `.md` 文件，`queries.json` 和 `manifest.json` 是验收配置，不用于上传。

“资料不足”和“模型超时/配置错误”是不同状态，后者不能当成正确拒答。列题数量指本次检索找到的数量，不代表全知识库的题目总数。

## 目录结构

```text
backend/
  app/                 # 中文注释的后端代码
    migrations/        # 编号 SQL 迁移
  tests/               # 规则、适配器及真实数据库集成测试
  pyproject.toml
  uv.lock
frontend/
  src/components/      # 知识库、问答、原文来源面板
  e2e/                 # Chrome 端到端测试
  package-lock.json
fixtures/acceptance/v1/ # 固定中文合成面经、查询真值与哈希清单
scripts/               # 本机启动入口、验收及浏览器测试服务
doc/                   # PRD、架构、时序图、API、验证记录
data/                  # 运行时目录，不进入版本控制
```

## 测试

无数据库的快速验证：

```bash
backend/.venv/bin/pytest backend/tests -m 'not postgres' -q
backend/.venv/bin/ruff check --config backend/pyproject.toml backend scripts main.py
npm --prefix frontend run build
backend/.venv/bin/python scripts/evaluate.py verify-fixtures
npm --prefix frontend run docs:check
```

真实 PostgreSQL/pgvector 测试，可选择如下方式之一。

- 设置 `TEST_DATABASE_URL` 指向**专用测试数据库**。测试会创建随机 schema、使用真实向量扩展，结束后仅删除本次 schema；数据库角色需要创建 schema/扩展权限。
- 在支持 pgserver wheel 的 Python 3.12 环境安装可选测试依赖，临时启动 PostgreSQL：

```bash
uv sync --project backend --python 3.12 --extra dev --extra models --extra postgres-test
LC_ALL=C RUN_POSTGRES_TESTS=1 backend/.venv/bin/pytest backend/tests -q
```

浏览器测试复用本机 Chrome，使用临时 PostgreSQL 和确定性假模型，**不消耗 DeepSeek 额度**：

```bash
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

没有 Chrome 的 CI 环境可安装 Playwright Chromium 并调整 `frontend/playwright.config.ts` 的 `channel`。`docs:check` 也使用本机 Chrome 解析 Mermaid 图。浏览器测试需要上述 `postgres-test` 依赖；测试服务在 8123 端口启动，不能替代正常启动入口。

真实中文召回及生成验收见 [验收说明](doc/acceptance.md)。固定样本的初始阈值为 0.5，**尚未标定，不能把自动化流程测试当成真实召回质量报告**。

## 数据流、备份与恢复

- 原文件副本：`data/uploads`，使用内部 UUID 命名；日志：`data/logs`，滚动保留。
- 模型缓存：`data/models`，含锁定 revision；可通过 `MODEL_CACHE_DIR` 调整。
- 数据库：Docker 模式使用命名卷 `rag_postgres`；无 Docker 模式使用 `data/postgres`，其中保存文本、向量和任务状态。
- Embedding 在本地完成；用户问题及选中的证据片段发送到 DeepSeek。应用不提供互联网搜索工具，不上传整份原文件。报告属于用户主动生成的验收产物，可能含答案与片段，应像原资料一样管理。
- 普通日志仅含请求 ID、片段 ID、耗时、错误码，不保存完整问题、原文或 API Key。完整问答、实际模型消息、原始响应和证据快照保存在本机 PostgreSQL 专用表，默认保留 30 天；备份也包含这些业务内容。

停止应用时先终止前后端，再运行 `docker compose stop`。服务重启后继续待处理任务；遗留 processing 任务变为 `INTERRUPTED` 失败，页面可重试。旧版本仍保持可用。

备份时停止应用写入，将数据库备份与 `data/uploads`、模型锁文件、配置中的非密钥参数一起保存：

```bash
docker compose exec -T postgres pg_dump -U rag -d rag > /tmp/rag-backup.sql
```

上面的 pg_dump 命令用于 Docker 模式。无 Docker 模式可先正常停止应用及数据库，再配套备份整个 `data/postgres` 和 `data/uploads`，不要在数据库仍写入时直接复制数据目录。原生数据目录的恢复需使用兼容的 PostgreSQL 版本。

恢复需要配套的原文件副本，不能只恢复向量。`docker compose down` 保留数据卷；不要把带 `-v` 的删除卷命令当成日常停止方式。

模型 revision、归一化/编码方式或切块配置变化时，旧向量空间会被检测为不兼容。MVP 的重建流程是：先备份/导出原文件，删除知识库全部文档，切换配置，再重新导入。暂不提供跨模型的在线无缝迁移。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 页面打不开 | 确认服务终端仍在运行；开发模式访问 5173，构建模式访问 8000 |
| `Address already in use` | 端口已有服务，优先访问现有项目或在对应终端正常停止它，再重新启动 |
| 页面能打开，但列表提示数据库不可用 | Docker 模式检查容器和迁移；无 Docker 模式必须使用完整 pgserver 启动命令，单独运行 main.py 不会接管它的数据库连接 |
| 模型下载提示访问权限不足 | 在 Hugging Face 模型页接受条款并授权，提供有效 HF_TOKEN 后重新运行 `rag download-model` |
| 入库显示 `MODEL_NOT_READY` | 检查 models 依赖、模型权重和 SHA 锁文件；模型下载成功后点击失败任务的“重试” |
| 问答提示 DeepSeek 配置错误 | 检查后端 `.env` 中的 Key、模型名和 API 地址，修改后重启 |
| 有文件但问答提示资料不足 | 先确认已入库成功，再查看本地日志；阈值尚未标定或召回证据不足时也可能拒答 |
| 系统 Python 是 3.14 | 使用 uv 安装 Python 3.12 的后端环境，不复用根目录旧 `.venv` |
| 内存紧张或入库较慢 | 将 EMBEDDING_BATCH_SIZE 降为 1，关闭不需要的应用；首次加载与批量入库可能较慢 |

排错时查看 `data/logs/rag.log`，根据请求 ID 定位阶段。不要把真实 API Key 或完整敏感面经贴进公开问题报告。

## 开发时维护文档

新增 API 同步修改 `doc/api.md`；调整事务、任务或模块边界同步修改 `doc/architecture.md`；修改交互顺序与异常分支同步修改 `doc/sequence-diagrams.md`；业务行为变更同时补回归测试和验证记录。注释解释设计原因、业务不变量和边界，不逐行翻译代码。

前端代码用 `npm --prefix frontend run format` 格式化，`format:check` 检查格式；Python 使用 Ruff。仓库根目录的 `AGENTS.md` 保存这些长期维护约定。

## 问答记录与排查

从本次升级开始，每个通过请求格式校验的问答都会保存记录，包括未调用模型的空库/无候选情况；不会补录升级前的历史问答。记录按单次请求保存，尚无历史对话页面，不会把旧对话作为多轮上下文回传。密钥、Authorization、Cookie、HF Token 和带凭据的 URL 会脱敏，普通 `rag.log` 不保存这些完整业务内容。

首次升级已有数据库时先运行 `backend/.venv/bin/rag migrate`，然后重启服务；本机入口 `backend/.venv/bin/python scripts/run_local.py` 会先执行迁移。迁移只新增表与索引，保留现有文档和向量。

DBeaver 连接本机 **127.0.0.1:5433 / postgres / 用户 postgres / 空密码 / SSL 关闭**（本机原生模式）。刷新 `public` 下的表：

| 表 | 查看内容 |
| --- | --- |
| `query_records` | 原始问题、查询参数、最终响应、执行状态、错误步骤、未调用模型的原因、到期时间 |
| `llm_calls` | 每次尝试实际发送的 messages、非密钥参数、证据快照、原始 HTTP 响应、助手内容、模型标识、用量、错误与耗时 |
| `query_logs` | 检索 ID、阶段耗时和错误码的轻量摘要，沿用原有用途 |

用接口返回的 `request_id`（或响应头 `X-Request-ID`）关联查询。先看问答状态和失败步骤，再按尝试顺序查看模型调用：

```sql
SELECT request_id, started_at, status, current_stage, error_stage, error_code,
       model_called, no_call_reason, user_query, final_response
FROM query_records ORDER BY started_at DESC LIMIT 50;

-- 将下面 UUID 替换为本次请求 ID。
SELECT attempt_no, status, http_status, error_stage, error_code, duration_ms,
       request_messages, request_parameters, evidence_snapshot,
       raw_response, assistant_message, response_model, usage
FROM llm_calls WHERE request_id = '00000000-0000-0000-0000-000000000000'
ORDER BY attempt_no;
```

- `retrieval`：检查文档激活状态、Embedding 模型/指纹及向量检索；`context_prepare`：检查上下文预算。
- `llm_transport`：检查连接、超时、鉴权和服务状态；503/429 等临时错误最多重试一次，每次各占一行。
- `response_parse`：HTTP 成功但 JSON 或结构无效；`output_validation`：引用、原题或回答结构校验失败；`source_validation`：生成期间来源被删除/替换。
- `query_start` / `call_start` / `response_save` / `query_finish` 等：记录保存失败，接口返回 `AUDIT_UNAVAILABLE`（503）；写入前失败不会发送远程请求，写入后失败不会伪装回答已保存。此时也看 `data/logs/rag.log` 中同一请求 ID；数据库故障可能导致该请求没有完整记录。

`model_called=false` 时，`empty_knowledge_base` 表示空库，`no_candidates` 表示检索/阈值过滤无候选，`context_budget_exhausted` 表示证据未进入预算，`llm_configuration` 表示调用前配置缺失。`model_called=true` 表示调用尝试已登记；进程中断时供应商是否已执行可能未知，以 `interrupted` 状态表示，不自动重发以免重复计费。

也可在项目根目录通过本地命令查看和清理（输出会含完整业务内容）：

```bash
backend/.venv/bin/rag records --request-id 00000000-0000-0000-0000-000000000000
# 默认仅清理到期且已结束的问答；dry-run 只统计。
backend/.venv/bin/rag purge-records --dry-run
backend/.venv/bin/rag purge-records
# 按请求清理（将 UUID 替换为实际 ID）。
backend/.venv/bin/rag purge-records --request-id 00000000-0000-0000-0000-000000000000 --dry-run
# 按带时区的开始时间清理：[after, before)，可只指定 before。
backend/.venv/bin/rag purge-records --after '2026-10-01T00:00:00+08:00' --before '2026-10-02T00:00:00+08:00' --dry-run
# 按历史文档版本 ID 清理包含该证据的整条问答及其所有调用。
backend/.venv/bin/rag purge-records --document-id 00000000-0000-0000-0000-000000000000 --dry-run
# 手动恢复失去租约的 running/pending，不发模型请求。
backend/.venv/bin/rag recover-records
```

显式清理确认范围后去掉 `--dry-run` 执行。所有清理都跳过 running 记录；清理问答会级联删除其全部调用，轻量 `query_logs` 不受影响。清理后在 DBeaver 刷新表数据。

`.env` 中 `AUDIT_RETENTION_DAYS=30` 控制新记录到期时间，`AUDIT_CLEANUP_INTERVAL_SECONDS=3600` 控制维护间隔；修改配置后重启。默认后台 worker 启动时和定期维护时标记失去会话租约的请求为 `interrupted` 并清理到期终态记录；长入库任务可能延后维护。关闭 `WORKER_ENABLED` 时需手动运行恢复/清理命令。更改留存天数不会回写已有记录的到期时间。

**删除知识文档只删除当前原文件和检索索引，历史证据快照仍保留至到期。** 彻底移除某资料时，先删除文档，再用该文档版本 ID 执行 `purge-records --document-id ...`；同名替换也会留下旧版 ID 的历史快照，应逐版本清理。历史快照从不参与当前召回。清理整条问答会同时移除相关问题、回答、messages 和所有快照，可能一并删除该次查询引用的其他文件内容。数据库备份、已导出的记录和验收报告需另行按同样要求管理；这是逻辑删除，不承诺磁盘或备份的物理擦除。
