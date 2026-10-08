# backend（DevMind-AI 后端服务）

FastAPI + asyncpg + PostgreSQL 16 + Redis + PyJWT。

## 功能

- 用户注册 / 登录 / 手机验证码 / 退出登录（JWT）
- 知识库 CRUD、成员角色与租户隔离
- 知识库文档查看（按成员权限过滤的列表/详情）
- 文档上传、MinIO 存储、解析分块、BGE-M3/Milvus 索引与任务进度
- Query 改写、Milvus 稠密/稀疏混合检索、BGE 重排序与 DashScope 流式回答
- 会话、消息、引用、可信度和检索运行持久化
- 会话搜索/重命名/软删除、回答收藏与用户工作台偏好持久化
- 高频 FAQ 查看（列表/详情/问题搜索）
- Redis 单独 db 缓存用户问题，解决缓存穿透/雪崩/击穿
  - db 1：短信验证码
  - db 2：用户问题缓存（空值短 TTL + 随机 TTL + Redis 锁指数退避）
  - db 3：JWT 黑名单

## 启动

```powershell
# 在项目根目录执行
uv sync
docker compose -f docker/docker-compose.yml up -d postgres redis minio etcd milvus clamav
uv run python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 15200 --reload

# 另开一个终端；上传、重新索引和删除任务由独立 Worker 执行
uv run python -m backend.app.worker
```

PostgreSQL 首次创建数据卷时会自动执行根目录 `sql/schema.sql`。个人配置可从 `config.local.ini.example` 复制为 `config.local.ini`。

已有 PostgreSQL 数据卷统一使用带版本账本的迁移工具升级：

```powershell
uv run python scripts/migrate.py plan
uv run python scripts/migrate.py apply
```

工具将版本、文件名和 SHA-256 写入 `schema_migrations`。已执行的迁移文件不得修改，只能新增更高编号文件。

启用完整文档索引前启动 MinIO 与 Milvus，并准备 BGE-M3：

```powershell
docker compose -f docker/docker-compose.yml up -d minio etcd milvus
uv run python -m internal_kb_qa.scripts.download_models bge-m3
uv run python -m internal_kb_qa.scripts.download_models bge-reranker-v2-m3
```

只验证上传、解析和 PostgreSQL 分块时可临时设置 `$env:MILVUS_INDEXING_ENABLED='false'`；生产环境不要关闭。

## 主要接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/auth/send-code | 发送手机验证码 |
| POST | /api/v1/auth/register | 注册 |
| POST | /api/v1/auth/login | 账号密码登录 |
| POST | /api/v1/auth/login/sms | 手机验证码登录 |
| POST | /api/v1/auth/logout | 退出登录 |
| GET | /api/v1/me | 当前用户 |
| GET | /api/v1/me/profile | 当前用户完整档案与偏好 |
| PATCH | /api/v1/me/profile | 保存当前用户档案与偏好 |
| GET/PATCH | /api/v1/me/preferences | 读取/保存工作台偏好 |
| GET | /api/v1/organization/users | 搜索当前组织成员 |
| GET/POST | /api/v1/knowledge-bases | 查询/创建知识库 |
| GET/PATCH/DELETE | /api/v1/knowledge-bases/{id} | 详情/修改/归档知识库 |
| GET | /api/v1/knowledge-bases/{id}/members | 查看知识库成员 |
| PUT/DELETE | /api/v1/knowledge-bases/{id}/members/{user_id} | 设置角色/移除成员 |
| GET/POST | /api/v1/documents | 查询/上传文档 |
| GET/PATCH/DELETE | /api/v1/documents/{id} | 详情/修改/软删除文档 |
| POST | /api/v1/documents/{id}/reindex | 重新索引文档 |
| GET | /api/v1/documents/{id}/chunks | 文档分块预览 |
| GET | /api/v1/documents/{id}/task | 文档任务进度 |
| POST | /api/v1/documents/{id}/task/retry | 人工重试失败或死信任务 |
| GET/POST | /api/v1/sessions | 查询/创建会话 |
| GET/PATCH/DELETE | /api/v1/sessions/{id} | 会话消息详情/重命名/软删除 |
| GET | /api/v1/favorites | 收藏回答列表 |
| PUT/DELETE | /api/v1/favorites/messages/{message_id} | 收藏/取消收藏回答 |
| POST | /api/v1/messages | 创建问题和 AI 回答占位消息 |
| GET | /api/v1/messages/{id} | 查询消息 |
| WS | /api/v1/chat/stream | RAG 流式回答 |
| GET | /api/v1/messages/{id}/citations | 回答引用列表 |
| GET | /api/v1/citations/{id} | 引用片段详情 |
| POST | /api/v1/retrieval/test | 混合检索与重排测试 |
| GET | /api/v1/knowledge/docs | 知识库文档列表 |
| GET | /api/v1/knowledge/docs/{id} | 文档详情 |
| GET | /api/v1/faq | FAQ 列表 |
| GET | /api/v1/faq/{id} | FAQ 详情 |
| POST | /api/v1/faq/search | 用户问题搜索（走 Redis 缓存） |

业务接口成功和失败响应均携带 `request_id`。应用和 Worker 输出结构化 JSON 日志；Prometheus 从 `/metrics` 采集 HTTP、RAG 和文档任务指标。生产环境必须配置 `JWT_SECRET`、`METRICS_TOKEN` 和 `CORS_ORIGINS`，密钥建议通过对应的 `*_FILE` 变量挂载。

完整依赖、后端和前端均启动后，可运行真实端到端联调：

```powershell
uv run python tests/live_e2e.py
```

## RAG 性能参数与评测

本机 CPU 默认只允许一个本地模型任务运行，避免 BGE-M3 与 Reranker 并发争抢 CPU：

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `RAG_LOCAL_MODEL_CONCURRENCY` | `1` | 本地 Embedding/Reranker 总并发数；CPU 环境不要盲目调大 |
| `RAG_LLM_CONCURRENCY` | `8` | 外部 LLM 流式生成并发上限 |
| `RAG_MODEL_TIMEOUT_SECONDS` | `120` | 本地模型单阶段超时 |
| `RAG_LLM_TIMEOUT_SECONDS` | `120` | LLM 请求超时 |
| `BGE_DEVICE` | `cpu` | BGE-M3 运行设备；GPU 部署可设为 `cuda` |
| `DOCUMENT_WORKER_POLL_SECONDS` | `1` | Worker 空闲轮询间隔 |
| `DOCUMENT_WORKER_HEARTBEAT_SECONDS` | `10` | 任务心跳间隔 |
| `DOCUMENT_TASK_LEASE_SECONDS` | `60` | Worker 租约有效期 |
| `DOCUMENT_TASK_TIMEOUT_SECONDS` | `1800` | 单个文档任务超时 |
| `DOCUMENT_TASK_MAX_ATTEMPTS` | `3` | 自动执行总次数，耗尽后进入死信 |
| `DOCUMENT_TASK_RETRY_BASE_SECONDS` | `10` | 指数退避基础秒数 |
| `CLAMAV_ENABLED` | `false` | 开发环境可关闭；非开发环境必须为 `true` |
| `CLAMAV_HOST` / `CLAMAV_PORT` | `127.0.0.1` / `3310` | ClamAV `clamd` 地址 |
| `LOGIN_LOCK_THRESHOLD` | `5` | 连续密码失败锁定阈值 |
| `LOGIN_LOCK_SECONDS` | `900` | 临时锁定时间 |
| `LOGIN_IP_LIMIT` / `LOGIN_ACCOUNT_LIMIT` | `30` / `10` | 15 分钟登录限流 |
| `SMS_SEND_DAILY_LIMIT` | `10` | 单手机号每日验证码发送上限 |
| `JWT_REQUEST_LIMIT` | `300` | 单用户每分钟已认证请求上限 |

运行生产参数质量回归：

```powershell
uv run python -m internal_kb_qa.evaluation.run_quality_eval --top-k 5 --rerank-top-n 3 --output docs/eval/rag_quality_optimized_retrieval.json
uv run python -m internal_kb_qa.evaluation.run_answer_eval --output docs/eval/rag_quality_optimized_final.json
```
