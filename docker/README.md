# docker（T11 私有化部署）

所有服务已合并到单文件：`docker/docker-compose.yml`

包含服务：

- PostgreSQL 16
- Redis
- ClamAV
- etcd
- MinIO
- Milvus
- Neo4j

启动：

```powershell
cd docker
docker compose up -d
```

PostgreSQL 首次创建数据卷时自动执行根目录的 `sql/schema.sql`。如果数据卷已经存在，按 `sql/migrations/` 文件编号顺序执行迁移；不要为了升级删除现有数据卷。

本地开发端口：PostgreSQL `15432`、Redis `16379`、ClamAV `3310`、MinIO API `9000`、MinIO Console `9001`、Milvus `19530`。容器网络内部仍使用各服务标准端口。

> `base_app/` 和 `milvus_redis/` 下的旧 compose 文件已废弃，仅保留说明。

## 备份验收

从仓库根目录执行：

```powershell
$env:PYTHONPATH='.'
uv run python scripts/backup.py create --output backups
uv run python scripts/backup.py verify --input backups/devmind-YYYYMMDD-HHMMSS
uv run python scripts/backup.py restore-drill --input backups/devmind-YYYYMMDD-HHMMSS --cleanup
```

详细的维护窗口、保留策略和故障处置见 `docs/deployment/备份与恢复.md`。

## 生产部署

生产环境使用基础文件和生产覆盖文件组合：

```powershell
Copy-Item docker/production.env.example docker/production.env
uv run python scripts/release.py validate
uv run python scripts/release.py init
```

升级与回滚命令、密钥文件列表和验收用例见 `docs/deployment/管理员手册与发布验收.md`。
