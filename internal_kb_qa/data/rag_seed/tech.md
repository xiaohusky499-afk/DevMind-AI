---
title: 研发技术咨询高频问题与排查手册
category: tech
team: infra
system: internal_tech_kb
doc_type: faq
version: 1.0
security_level: public
last_updated: 2026-08-22
---

# 研发技术咨询高频问题与排查手册

本手册面向研发、测试、运维人员，覆盖环境安装、依赖配置、常见报错、接口调试和部署发布等高频技术问题。

## 一、环境安装与依赖配置

### 1. Python 项目环境初始化

使用 uv 管理 Python 依赖的推荐步骤：

```shell
cd 项目根目录
uv sync
```

不使用 uv 时，使用 venv：

```shell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

常见问题：依赖安装慢时，优先配置公司内网 PyPI 镜像源。

### 2. Java / Maven 项目配置

- JDK 版本以项目根目录 `.java-version` 或 `pom.xml` 为准。
- Maven 私服地址统一配置在 `~/.m2/settings.xml` 的 `mirrors` 节点中。
- 常见错误：`Could not transfer artifact`，通常表示私服地址不可达或账号无权限。

### 3. Node / npm 项目配置

- 前端项目统一使用 npm。
- 首次运行执行 `npm install`，如果出现依赖冲突，删除 `node_modules` 和 `package-lock.json` 后重新安装。
- 公司内网优先使用内部 npm registry。

## 二、常见报错排查

### MySQL 连接失败

现象：应用启动时提示 `Can't connect to MySQL server` 或 `Connection refused`。

排查步骤：

1. 确认 MySQL 服务已启动。
2. 确认配置文件中的 host、port、user、password 是否正确。
3. 使用 `telnet 数据库地址 3306` 检查网络连通性。
4. 确认数据库名已经创建。
5. 如果使用容器部署，确认容器端口映射和防火墙放行。

### Spring Boot Bean 创建失败

现象：`Error creating bean with name 'dataSource'`。

排查步骤：

1. 检查 `application.yml` 中的数据库连接配置。
2. 确认数据库账号有目标库的访问权限。
3. 确认数据库驱动依赖存在。
4. 查看完整堆栈最底层的 `Caused by` 信息。

### Git 推送失败

现象：`Permission denied (publickey)`。

排查步骤：

1. 确认本机已生成 SSH key：`ssh-keygen -t rsa -b 4096`。
2. 将公钥添加到 GitLab / GitHub 账号的 SSH Keys 中。
3. 使用 `ssh -T git@内部git地址` 测试连通性。
4. 确认远程地址使用 SSH 协议。

### 接口返回 503 Service Unavailable

可能原因：

- 上游服务未启动。
- 服务线程池被打满。
- 网关或负载均衡没有可用节点。
- 服务正在发布或重启。

排查顺序：先看服务健康检查，再看网关日志和服务日志，最后确认发布状态。

## 三、接口调试

### 常用接口调试步骤

1. 从接口文档确认 URL、请求方法、请求头和参数。
2. 先用 Postman 或 Apifox 发送最小可用请求。
3. 再逐步加入业务参数。
4. 出现 401 时优先检查 token 是否携带、是否过期。
5. 出现 403 时确认账号是否有所需权限。

### SSO Token 使用

- 登录后前端在请求头携带 `Authorization: Bearer <token>`。
- token 过期后需要重新登录。
- 退出登录后 token 会加入黑名单，不可继续使用。

## 四、部署与发布

### Docker 部署后端服务

1. 构建镜像：`docker build -t 应用名:版本号 .`
2. 启动依赖服务：`docker compose up -d`
3. 启动应用容器。
4. 发布后检查 `/health` 健康检查接口。
5. 观察应用日志，确认无启动异常。

### 发布失败处理

- 先确认构建日志中第一个报错位置。
- 再确认环境变量和配置文件是否完整。
- 发布失败时优先回滚到上一个稳定版本。

## 五、常见技术问答对

### 问：本地启动后端服务端口被占用怎么办？

答：先执行 `netstat -ano | findstr :端口号` 找到占用进程，再确认进程是否可关闭；如果可关闭，使用 `taskkill /PID 进程号 /F` 结束进程。

### 问：接口请求超时应该怎么排查？

答：先确认服务端是否收到请求，再查看服务日志耗时、数据库慢查询、Redis 连接情况和上游接口响应时间，逐段定位耗时。

### 问：数据库慢查询怎么处理？

答：先开启慢查询日志，找到执行时间最长的 SQL，再使用 `EXPLAIN` 分析执行计划，确认是否缺少索引，最后优化 SQL 或添加索引。

### 问：Redis 连接超时是什么原因？

答：常见原因包括 Redis 未启动、端口不通、密码错误、连接数打满、网络抖动。先使用 `redis-cli -h 地址 -p 端口 ping` 检查连通性。

### 问：前端访问后端跨域怎么办？

答：后端增加 CORS 配置，允许前端域名；开发环境也可以让 Vite 代理 `/api` 请求到后端服务。

### 问：Docker 容器启动后立刻退出怎么排查？

答：执行 `docker logs 容器名` 查看启动日志，重点看最后几行报错；常见原因有端口冲突、环境变量缺失、依赖服务未启动。
