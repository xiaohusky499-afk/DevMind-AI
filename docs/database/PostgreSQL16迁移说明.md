# PostgreSQL 16 迁移说明

DevMind AI 的正式业务数据库已从 MySQL 8 切换为 PostgreSQL 16。Redis、Milvus、MinIO 与前端 API 地址不受影响。

## 配置

推荐通过环境变量配置：

```text
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=15432
POSTGRES_USER=devmind
POSTGRES_PASSWORD=替换为本地强密码
POSTGRES_DB=internal_tech_kb
```

也可以直接使用：

```text
DATABASE_URL=postgresql://devmind:替换为本地强密码@127.0.0.1:15432/internal_tech_kb
```

生产环境必须修改示例密码，并通过密钥管理系统注入。

## 全新环境

```powershell
cd docker
docker compose up -d postgres redis milvus
```

PostgreSQL 首次创建数据卷时自动执行 `sql/schema.sql`。

## 已有 PostgreSQL 数据卷

初始化目录只会在空数据卷执行。已有数据卷按版本顺序执行增量迁移：

```powershell
Get-Content -Raw sql/migrations/002_knowledge_base_status.sql |
  docker compose -f docker/docker-compose.yml exec -T postgres psql -v ON_ERROR_STOP=1 -U devmind -d internal_tech_kb
Get-Content -Raw sql/migrations/003_document_tasks.sql |
  docker compose -f docker/docker-compose.yml exec -T postgres psql -v ON_ERROR_STOP=1 -U devmind -d internal_tech_kb
Get-Content -Raw sql/migrations/004_workspace_favorites_preferences.sql |
  docker compose -f docker/docker-compose.yml exec -T postgres psql -v ON_ERROR_STOP=1 -U devmind -d internal_tech_kb
```

`004` 创建回答收藏、用户工作台偏好和收藏时间索引，可重复执行。不要在已有数据卷上使用完整 `schema.sql` 代替版本迁移；生产部署应记录已执行版本。

## 已有 MySQL 数据

本次迁移不会删除旧 MySQL 数据卷，也不会自动复制旧数据。旧库存在有效数据时，应先备份，然后按以下顺序执行一次性 ETL：

1. 导出 `users`、`faq`、`documents`、`conversations`、`feedback` 和 `tickets`。
2. 为旧自增 ID 建立到 PostgreSQL UUID 的映射。
3. 先导入组织和用户，再导入知识库、文档、会话与关联表。
4. 对文档重新构建 Milvus 索引，不直接复制旧向量主键。
5. 比较每张表行数、外键缺失数和抽样内容后再切换流量。

旧数据量和来源明确后再编写一次性 ETL 脚本，避免把未经验证的自动迁移逻辑放进正式服务。

## 验证

```powershell
uv run python -m pytest tests/test_backend_api_contract.py -q
docker compose -f docker/docker-compose.yml config --quiet
docker compose exec postgres psql -U devmind -d internal_tech_kb -c "select version();"
```

完整服务启动后运行真实端到端联调：

```powershell
uv run python tests/live_e2e.py
```

该脚本验证前端路由、注册登录、知识库、文档上传与 Milvus 索引、检索重排、WebSocket 回答、引用、收藏、偏好、历史和退出令牌失效。
