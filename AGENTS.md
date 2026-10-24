# 仓库开发约定

## 产品与文档

- 以 `doc/agent_interview_rag_mvp_prd.md` 为产品基线。
- 代码注释、模块说明和设计文档使用中文。重点解释业务规则、不变量和设计原因，不逐行翻译语法。
- 修改模块职责、数据模型、事务或任务状态时，同步维护 `doc/architecture.md`。
- 修改交互顺序、失败分支、替换/删除/恢复语义时，同步维护 `doc/sequence-diagrams.md`。
- 修改接口时同步维护 `doc/api.md`、前端类型及相关测试；实际验证结果记入 `doc/verification.md`。

## 架构边界

- FastAPI 路由只负责装配、参数和 HTTP；SQL 在 Repository，业务规则在服务模块。
- PostgreSQL + pgvector 是持久化真值，不以 SQLite、内存字典替换生产存储。
- EmbeddingGemma 固定不可变 revision、768 维，运行时禁止隐式联网下载。
- DeepSeek Key 只在后端，禁止加入前端、日志、测试夹具或版本控制。
- 旧版在新版处理完成前保持有效；删除后迟到的 worker 结果不能激活文档。
- 外部服务故障为 error，不能当作资料不足；模型引用始终由服务端校验。

## 验证

在项目根目录运行：

```bash
backend/.venv/bin/ruff check --config backend/pyproject.toml backend scripts main.py
backend/.venv/bin/ruff format --config backend/pyproject.toml --check backend scripts main.py
backend/.venv/bin/pytest backend/tests -m 'not postgres' -q
npm --prefix frontend run build
npm --prefix frontend run docs:check
```

涉及持久化和 API 的改动需执行真实数据库测试；可使用专用 TEST_DATABASE_URL，或安装 postgres-test 可选依赖后执行：

```bash
LC_ALL=C RUN_POSTGRES_TESTS=1 backend/.venv/bin/pytest backend/tests -q
```

涉及用户操作的功能改动还需运行 `npm --prefix frontend run test:e2e`。该命令使用临时数据库和假模型，不代表真实语义效果验收。浏览器测试和文档图校验需要本机 Chrome。

## 验收资料

- 固定真值在 `fixtures/acceptance/v1`，区分 calibration 与 acceptance。
- 不用验收集调整阈值，不用模型自身答案生成测试真值。
- 修改已冻结样本时创建新版本，不通过重写旧 manifest 掩盖变化。
- API Key、模型访问权限缺失时继续可独立验证的工作，并明确记录尚未执行的真实服务检查。
