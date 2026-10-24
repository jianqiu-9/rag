# 核心流程时序图

对应当前后端模块与 PRD V1.2。图中 DB 事务均为短事务；本地推理与远程请求不占用版本切换锁。

## 1. 上传与后台入库

```mermaid
sequenceDiagram
    autonumber
    actor U as 用户
    participant UI as React
    participant API as FastAPI
    participant FS as FileStore
    participant DB as PostgreSQL
    participant W as IngestionWorker
    participant E as 本地 EmbeddingGemma
    U->>UI: 选择 MD/TXT
    UI->>API: POST /api/documents (multipart files)
    API->>API: 校验文件名、大小、UTF-8、哈希
    API->>FS: 临时写入、fsync、原子 rename
    API->>DB: 事务：去重、同名冲突检查、插入 pending
    alt 内容哈希已存在
        DB-->>API: 已有版本 ID
        API->>FS: 删除本次冗余副本
        API-->>UI: 200 + duplicate=true
    else 新任务
        DB-->>API: 新版本 ID
        API-->>UI: 202 + pending
        W->>DB: 领取 pending，标记 processing，尝试号+1
        W->>FS: 读取原文件
        W->>W: 按问答切分并保留行号
        W->>E: tokenizer 检查，超长块继续切分
        loop 按小批量处理
            W->>DB: 检查任务尚未删除
            W->>E: encode_document
            E-->>W: 768 维归一化向量
        end
        W->>DB: 事务：验证尝试号和向量指纹，写块并激活
        DB-->>W: completed + active
    end
    loop 页面存在未完成任务时，每 2 秒
        UI->>API: GET /api/documents
        API->>DB: 获取最新版本与旧版可用标志
        DB-->>UI: 文档状态
    end
```

多文件上传逐文件处理，允许部分成功；整个批次返回 207，每项有独立 status_code。

## 2. 同名替换与失败保留旧版

```mermaid
sequenceDiagram
    autonumber
    participant UI as React
    participant API as DocumentService
    participant DB as PostgreSQL
    participant W as Worker
    participant FS as FileStore
    Note over DB: 旧版 completed + active
    UI->>API: 上传同名不同内容
    API->>FS: 保存新版副本
    API->>DB: 新版 pending + inactive，关联逻辑文档
    API-->>UI: 202 + 新版本 ID
    Note over DB,W: 此阶段检索仍读取旧版
    W->>W: 解析与向量化新版
    alt 新版全部成功
        W->>DB: 开始事务并获取写锁
        W->>DB: 写入新块及向量
        W->>DB: 删除旧版与旧块，登记旧文件清理
        W->>DB: 新版 completed + active，提交
        Note over DB: 提交后仅检索新版
        W->>FS: 删除旧原文件副本
        alt 文件删除失败
            Note over DB,FS: 保留 file_gc，后续轮询重试
        end
    else 解析或模型失败
        W->>DB: 新版 failed，记录安全错误信息
        Note over DB: 旧版仍 completed + active
        UI->>API: 轮询状态
        API-->>UI: 新版失败，has_active_version=true
        UI->>UI: 提示失败且旧版本仍可检索
    end
```

## 3. 查询、证据判断与原文定位

```mermaid
sequenceDiagram
    autonumber
    actor U as 用户
    participant UI as React
    participant Q as QueryService
    participant E as 本地 EmbeddingGemma
    participant DB as PostgreSQL/pgvector
    participant L as 远程 DeepSeek Flash
    participant FS as FileStore
    U->>UI: 输入问题或列题请求
    UI->>Q: POST /api/query
    Q->>DB: 持有会话租约，保存 running 问答及原始问题
    Q->>Q: 解析数量和候选 TopK
    Q->>DB: 是否存在有效文档
    alt 空知识库
        Q->>DB: 保存 insufficient 与 empty_knowledge_base
        Q-->>UI: insufficient，无需加载模型
    else 有有效资料
        Q->>E: encode_query，检查 2048 token 上限
        E-->>Q: 768 维 Query 向量
        Q->>DB: 校验向量指纹，余弦阈值过滤并召回
        DB-->>Q: 候选块、原题、来源行号
        Q->>Q: 按保守上下文预算保留完整证据
        alt 无足够候选进入上下文
            Q->>DB: 保存 insufficient 与未调用原因
            Q-->>UI: insufficient，不调用远程模型
        else 有证据
            Q->>DB: 保存 pending 调用、实际 messages、参数和证据快照
            Q->>L: 问题与证据（无搜索工具）
            L->>L: 先判断充分性，再基于原文生成 JSON
            L-->>Q: answered 或 insufficient
            Q->>DB: 保存原始响应、助手内容、模型标识和 usage
            Q->>Q: 校验 JSON、原题、数量、答案与引用 ID
            Q->>DB: 再次检查引用仍属于 active 版本
            alt 来源已被删除或替换
                Q->>DB: 标记调用及问答 error，保留原始响应
                Q-->>UI: 409 SOURCE_CHANGED，可重新查询
            else 来源有效
                Q->>DB: 保存最终业务响应并结束问答
                Q-->>UI: 回答、原题、真实来源
            end
        end
    end
    Q->>DB: 释放会话租约，记录轻量 query_logs
    U->>UI: 点击引用
    UI->>Q: GET /api/documents/{id}/source
    Q->>DB: 检查版本仍 active
    Q->>FS: 读取原文
    Q->>DB: 再次检查来源有效性
    Q-->>UI: 原文件文本
    UI->>UI: 按 line_start / line_end 高亮
```

日志在服务 `finally` 中写入，先于 HTTP 实际发送；图中放在流程末尾表示所有结果分支都会执行。

## 4. 远程故障与总超时

```mermaid
sequenceDiagram
    autonumber
    participant Q as QueryService
    participant A as DeepSeek 适配器
    participant L as DeepSeek API
    Q->>A: 请求生成
    Note over A,L: 外层总时间预算默认 30 秒
    A->>L: 第一次 HTTP 请求
    alt 429、5xx 或临时网络故障
        A->>A: 预算内退避 0.2 秒
        A->>L: 最多重试一次
        alt 重试仍失败
            A-->>Q: 503 LLM_UNAVAILABLE
        else 成功
            L-->>A: JSON 响应
            A-->>Q: 结构化结果
        end
    else 鉴权或调用配置错误
        A-->>Q: 503 LLM_CONFIGURATION，不自动重试
    else JSON/结构错误
        A-->>Q: 502 INVALID_GENERATION，不自动重试
    else 任何阶段耗尽总预算
        A-->>Q: 504 LLM_TIMEOUT
    end
    Note over Q: 技术故障均为 error，不当作 insufficient
```

## 5. 重启、恢复与手动重试

本机模式在以下 FastAPI 生命周期之前，由 `scripts/run_local.py` 确保 PostgreSQL 的 loopback TCP 监听和迁移就绪；必要的数据库重启在启动 worker 之前完成。


```mermaid
sequenceDiagram
    autonumber
    participant P as FastAPI 进程
    participant W as Worker
    participant DB as PostgreSQL
    participant UI as React
    P->>W: lifespan.start
    W->>DB: 尝试取得单 worker 会话锁
    alt 已有消费者持锁
        W->>W: 等待后再竞争
    else 成功取得
        W->>DB: 遗留 processing → failed / INTERRUPTED
        W->>DB: 继续领取 pending
    end
    UI->>DB: 经 API 查询文档状态
    DB-->>UI: INTERRUPTED + 错误说明
    UI->>P: POST /api/documents/{id}/retry
    P->>DB: 事务校验 failed、清理暂存块、置 pending
    P-->>UI: 202
    W->>DB: 领取，attempt_count + 1
    W->>W: 重新解析与向量化
    W->>DB: 带尝试号完成任务
    Note over W,DB: 旧尝试迟到的 complete/fail 均无法覆盖新尝试
```

## 6. 删除正在处理或生成中的文档

```mermaid
sequenceDiagram
    autonumber
    participant UI as React
    participant A as API
    participant DB as PostgreSQL
    participant W as Worker
    participant Q as QueryService
    participant FS as FileStore
    Note over W: 已在事务外执行本地推理
    Note over Q: 可能已把旧证据发给 DeepSeek
    UI->>A: DELETE /api/documents/{version_id}
    A->>DB: 事务删除逻辑文件及全部版本/块，登记 file_gc
    DB-->>A: 提交，立即不再参与检索
    A->>FS: 尝试删除文件副本
    A-->>UI: 204
    W->>DB: 检查任务或提交完成
    DB-->>W: 文档不存在，不激活
    Q->>DB: 返回答案前检查引用有效性
    DB-->>Q: 来源已失效
    Q-->>UI: 409 SOURCE_CHANGED
```

已经发送给 DeepSeek 的证据无法因后续删除而撤回；页面和 README 的远程数据流告知覆盖这一边界。已返回浏览器的历史文本也不会被数据库删除自动擦除；发起新查询或重新展开来源时会重新校验。

## 8. 逐次调用审计及写入失败

```mermaid
sequenceDiagram
    autonumber
    participant Q as QueryService
    participant A as AuditStore
    participant DB as PostgreSQL
    participant L as DeepSeek
    Q->>A: begin(request_id, 原始问题, 参数)
    A->>DB: 持有会话锁，短事务插入 running
    loop 最多两次 HTTP 尝试
        Q->>A: start_call(实际 payload, 证据, attempt_no)
        A->>DB: 短事务插入 pending，标记 model_called
        alt 登记失败
            A-->>Q: AUDIT_UNAVAILABLE
            Note over Q,L: 本次不发送 HTTP
        else 登记成功
            Q->>L: 发送该次请求
            alt 收到 HTTP 响应
                L-->>Q: 响应原文及状态
                Q->>A: 先保存原始响应，再解析和校验
                A->>DB: raw_response、assistant_message、model、usage
                alt 写入失败
                    A-->>Q: AUDIT_UNAVAILABLE，不返回成功答案
                else 临时故障且仍可重试
                    Q->>A: 标记本次 error
                    Q->>Q: 预算内退避，再建立新 attempt
                else 输出及来源校验通过
                    Q->>A: 调用 succeeded，保存最终业务响应
                else 格式、引用或来源无效
                    Q->>A: 调用和问答 error，保留原始响应
                end
            else 连接失败或超时
                Q->>A: 本次 error，原始响应为空
            end
        end
    end
    Q->>A: 结束问答并关闭租约连接
```

两个错误层次独立：远程调用成功而最终业务响应保存失败时，调用可为 succeeded、问答为 error。无法记录错误本身时仍返回 503，普通日志只记录请求 ID 和安全标记。

## 9. 中断恢复、留存及文档清理

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker 或本地管理命令
    participant A as AuditStore
    participant DB as PostgreSQL
    W->>A: recover_interrupted
    A->>DB: 锁定 running 行，SKIP LOCKED
    loop 每条 running 问答
        A->>DB: 尝试对应事务 advisory lock
        alt 仍有活跃会话租约
            DB-->>A: 取锁失败，跳过
        else 租约已丢失
            A->>DB: 问答 interrupted，pending 调用 interrupted
            Note over W,DB: 结果未知，不自动重发模型请求
        end
    end
    W->>A: purge 到期记录或指定 ID/时间范围
    A->>DB: 筛选终态问答，始终跳过 running
    alt dry-run
        DB-->>W: 仅返回预计清理条数
    else 执行清理
        A->>DB: 删除 query_records，级联删除全部 llm_calls
        DB-->>W: 已清理问答数量
    end
    Note over W,DB: 删除知识文件仅删除原文件和索引，快照保留至到期
    Note over W,DB: 彻底清理需按旧文档版本 ID 删除关联整条问答
```
