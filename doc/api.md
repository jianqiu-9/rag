# HTTP API 约定

默认地址 `http://127.0.0.1:8000`。API 仅面向本机单用户。每个响应包含 `X-Request-ID`，查询体中的 `request_id` 与之相同。在线契约以 `/openapi.json` 和 `/docs` 为准。

## 1. 文档上传

`POST /api/documents`，multipart 字段名固定为 `files`，可重复提供，最多 20 份。

```bash
curl -F 'files=@fixtures/acceptance/v1/rag.md' http://127.0.0.1:8000/api/documents
```

成功示意：

```json
{
  "request_id": "请求UUID",
  "results": [
    {
      "document_id": "版本UUID",
      "logical_document_id": "逻辑文件UUID",
      "duplicate": false,
      "status_code": 202,
      "document": {
        "id": "版本UUID",
        "logical_document_id": "逻辑文件UUID",
        "filename": "rag.md",
        "sha256": "文件哈希",
        "status": "pending",
        "is_active": false,
        "attempt_count": 0,
        "error_code": null,
        "error_message": null
      }
    }
  ]
}
```

`document` 还包括 created_at、updated_at 和 replaces_document_id，均不包含 storage_path。单文件新任务 HTTP 202，重复文件 HTTP 200；单文件校验失败采用相应错误 HTTP 状态。多个文件返回 HTTP 207，各项 status_code 单独判断，不能把整批视为成功。

逐文件错误仍在 results 中，包含 filename、error_code、message、retryable、status_code。超过批量上限等请求级错误使用统一错误对象。

默认每文件 10 MB，仅接受 UTF-8 `.md`、`.txt`；格式错误 415，超限 413，编码/空文本错误 422，同名不同内容任务冲突 409。相同内容哈希返回已有版本，不重复排队。

## 2. 文档状态、重试、删除和原文

| 方法与路径 | 返回 |
| --- | --- |
| `GET /api/documents` | `{documents: [...]}`，每个逻辑文件最新尝试，含 has_active_version |
| `GET /api/documents/{id}` | 指定版本的公开字段，供轮询 |
| `POST /api/documents/{id}/retry` | HTTP 202，失败版本重新变为 pending |
| `DELETE /api/documents/{id}` | HTTP 204，无响应体；删除整个逻辑文件的全部版本 |
| `GET /api/documents/{id}/source` | `{document_id, filename, content}`，仅 active 版本允许读取 |

路径 ID 必须是 UUID。未找到版本返回 404；来源失效返回 409。新版本成功后旧版本会被清理，旧 ID 不再可用。重试只允许 failed，已完成或运行中返回 409。

前端在存在 pending/processing 版本时每 2 秒刷新列表，成功、失败后停止轮询。failed 加 has_active_version=true 表示替换失败但旧版可用。

## 3. 检索问答

`POST /api/query`，JSON 请求：

```json
{"query": "列出 5 道 RAG 面试题", "top_k": 8, "limit": 5}
```

- query：去除首尾空白后非空，最多 4000 字符，实际模型输入还受 2048 token 限制。
- top_k：可省略，默认环境配置 8，范围 1–50。
- limit：可省略；显式提供即表示列题，范围 1–20，优先于问题文本中的数量。
- 列题实际候选数为 `min(50, max(top_k, 3*N))`；普通问答使用 top_k。

回答 HTTP 200，字段如下：

| 字段 | 含义 |
| --- | --- |
| request_id | 请求 UUID |
| status | answered 或 insufficient |
| answer | 普通参考答案或资料不足提示；列题时为数量说明 |
| questions | 原题、原文支持的答案、source_chunk_ids、note；无答案时 answer=null |
| source_chunk_ids | 当前结果实际使用的片段 ID |
| sources | chunk_id、document_id、filename、section、line_start、line_end、excerpt |
| requested_count | 列题请求数量；普通问答为 null |
| actual_count | 实际列题数量，普通问答为 0 |
| notice | 数量不足时说明本次检索范围，其他情况可为空 |

来源行号从 1 开始，含首尾；文件内容展示使用 source 接口，不把 excerpt 当作完整原文件。

## 4. 统一错误与资料不足

资料不足使用 HTTP 200、`status=insufficient`，没有伪造题目或来源。技术错误使用非 2xx：

```json
{
  "status": "error",
  "request_id": "请求UUID",
  "error_code": "LLM_TIMEOUT",
  "message": "模型服务超时，请稍后重试",
  "retryable": true
}
```

| HTTP | 常见 code | 含义 |
| --- | --- | --- |
| 422 | INVALID_REQUEST / EMPTY_QUERY / QUERY_TOO_LONG / INVALID_LIMIT | 参数或模型长度不合法 |
| 404 | DOCUMENT_NOT_FOUND | 文档不存在或版本已被清理 |
| 409 | SOURCE_CHANGED / INDEX_PROFILE_MISMATCH / UPLOAD_IN_PROGRESS | 来源变化、模型配置冲突或同名任务冲突 |
| 502 | INVALID_GENERATION | JSON、引用或业务结果校验失败 |
| 503 | DATABASE_UNAVAILABLE / MODEL_NOT_READY | 本地依赖未就绪 |
| 503 | LLM_CONFIGURATION / LLM_UNAVAILABLE | 远程鉴权/配置问题或暂时不可用 |
| 503 | AUDIT_UNAVAILABLE | 问答或调用记录保存失败，不能声称回答已留存 |
| 504 | LLM_TIMEOUT | 整次生成请求耗尽总预算 |

是否可重试以 retryable 为准。配置错误需先修正配置；向量指纹变化需按架构文档重建索引。日志和前端均不展示远程错误原文或密钥。

## 5. 健康检查

`GET /api/health`：检查 PostgreSQL 连接和包含问答审计表的最新迁移，返回 status、embedding_model、llm_model、model_cached。model_cached 只表示锁文件存在，不保证所有权重完整可读；也不发付费请求验证 DeepSeek。

## 6. 单次问答审计

POST `/api/query` 在请求结构校验通过后先建立 query_records（保留原始问题及参数），每次实际远程调用分别记录 llm_calls，包括重试和非法响应。空库、无候选或预算不足也会有问答记录，但没有模型调用行。HTTP 格式校验失败不记录非法原始请求体。最终成功/错误响应在返回前写入 final_response；所有完整内容仅保存在本机数据库，鉴权凭据脱敏。

写入失败返回 `503 AUDIT_UNAVAILABLE`，retryable=true。发送前写入失败不会调用模型；发送后写入失败时供应商可能已执行，请根据 request_id 检查已有状态，重试可能产生新调用。普通 query_logs 写入失败不覆盖审计主记录或原始业务结果。API 不新增历史记录接口，使用 DBeaver 或本地 `rag records` 查看；默认保留 30 天，详见 README 的清理命令。
