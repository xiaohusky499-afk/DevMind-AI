"""T7 重排指标测试：对 bge-reranker-v2-m3 输出做 IR 指标量化评估。

指标：
  - MRR@k       （首条相关文档位置的倒数均值）
  - Recall@k    （前 k 内相关文档覆盖率）
  - Precision@k （前 k 内相关文档占比）
  - nDCG@k      （位置加权的归一化折损累计增益）

设计：
  - 内嵌 8 条高频技术问题评测集，每条 6 个候选片段（3 相关 + 3 干扰），
    相关片段与问题语义强相关、干扰片段为同主题但答非所问。
  - 调用 internal_kb_qa.core.reranker.rerank（真实 bge-reranker-v2-m3）。
  - 模型/torch 不可用时按仓库惯例 skipTest，不影响其它测试。

运行（项目根目录）：
    python tests/test_rerank_metrics.py
"""
from __future__ import annotations

import os
import sys
import unittest
from math import log2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

TOP_K = 3

# 验收阈值：按 bge-reranker-v2-m3 在评测集上的实测表现校准
# （2026-08-23 实测 MRR@3=1.0000 / Recall@3=0.7917 / Precision@3=0.7917 / nDCG@3=0.8380）。
# 门槛统一放在实测值下方留余量，避免压在边界上抖动。
THRESHOLDS = {
    "MRR@3": 0.95,
    "Recall@3": 0.75,
    "Precision@3": 0.75,
    "nDCG@3": 0.80,
}

# ---------------------------------------------------------------------------
# 1. 指标计算（纯函数，可单测）
# ---------------------------------------------------------------------------


def _rel(hit) -> bool:
    return hit.metadata.get("eval_role") == "relevant"


def mrr_at_k(ranked, k: int) -> float:
    """首个相关文档位置的倒数；前 k 无相关则记 0。"""
    for i, hit in enumerate(ranked[:k]):
        if _rel(hit):
            return 1.0 / (i + 1)
    return 0.0


def recall_at_k(ranked, k: int, n_relevant: int) -> float:
    if n_relevant <= 0:
        return 0.0
    hit = sum(1 for h in ranked[:k] if _rel(h))
    return hit / n_relevant


def precision_at_k(ranked, k: int) -> float:
    if k <= 0:
        return 0.0
    return sum(1 for h in ranked[:k] if _rel(h)) / k


def ndcg_at_k(ranked, k: int, n_relevant: int) -> float:
    """二元增益 nDCG@k：理想序把相关文档全部前置。"""
    dcg = sum(1.0 / log2(i + 2) for i, h in enumerate(ranked[:k]) if _rel(h))
    idcg = sum(1.0 / log2(i + 2) for i in range(min(n_relevant, k)))
    return dcg / idcg if idcg > 0 else 0.0


# ---------------------------------------------------------------------------
# 2. 评测集（8 条 × 6 候选：3 相关 + 3 干扰）
# ---------------------------------------------------------------------------

EVAL_SET = [
    {
        "query": "MySQL 连接池 max_connections 默认值是多少？",
        "relevant": [
            "MySQL 连接池 max_connections 默认值为 151，可在 my.cnf [mysqld] 段设置 max_connections=500 后重启生效。连接打满时用 SHOW PROCESSLIST 查看堆积连接并调大该值。",
            "wiki-mysql.md：连接池参数说明，max_connections 控制服务端最大并发连接数，默认 151；修改后需确认 open_files_limit 足够，避免 fd 耗尽。",
            "常见问题：客户端报 'Too many connections'，原因多为 max_connections 默认 151 过小，生产建议调至 500-1000 并配合 wait_timeout 缩短空闲占用。",
        ],
        "irrelevant": [
            "Redis 连接池配置：JedisPool maxTotal 控制客户端连接数，与 MySQL max_connections 无关，主要用于控制对 Redis 的并发访问。",
            "Nginx worker_connections 控制单 worker 最大并发连接数，默认 1024，是反向代理层参数，与数据库连接池无关。",
            "Java HikariCP 连接池 maximumPoolSize 控制应用侧连接上限，需与 MySQL 侧 max_connections 配合，但本题问的是数据库服务端默认值。",
        ],
    },
    {
        "query": "Redis 主从切换步骤，怎么手工触发 failover？",
        "relevant": [
            "runbook-redis-主从切换.md：主从切换前确认 repl 链路健康（INFO replication），随后在从库执行 CLUSTER FAILOVER 或 slaveof no one 提升为主，切换后更新业务侧连接配置。",
            "手工 failover 步骤：1) 检查从库复制延迟；2) 在主库写 CLUSTER FAILOVER 触发优雅切换；3) 验证新主可写、旧主降为从；4) 通知调用方刷新连接。",
            "故障场景预案：主库不可用时在从库执行 SLAVEOF NO ONE 强制提升，并重新配置其它从库指向新主，全程需业务侧配置中心同步。",
        ],
        "irrelevant": [
            "Redis 持久化说明：RDB 与 AOF 的开启方式与策略，主要解决重启后数据恢复问题，与主从切换无关。",
            "Redis 缓存穿透解决方案：布隆过滤器与空值缓存，用于应对热点 key 打穿，与 failover 无关。",
            "MySQL 主从复制配置：binlog + relay log 实现数据同步，虽同为高可用话题，但本题针对 Redis。",
        ],
    },
    {
        "query": "user-service 的 JWT 鉴权配置在哪里改？",
        "relevant": [
            "user-service 鉴权配置位于 user-service 仓库 config/auth.yml：jwt.secret 用于签名、token 有效期、白名单路径；修改后需重启 user-service 并同步网关校验密钥。",
            "auth 配置规范：密钥通过环境变量注入而非明文入库；轮换 jwt.secret 时需保证所有消费方（网关、内网服务）同步更新，否则验签失败返回 401。",
            "常见报错：本地启动 401，多为 auth.yml 中 jwt.secret 与网关不一致，或 exp 过期；排查先看网关是否透传 Authorization 头。",
        ],
        "irrelevant": [
            "Nginx 网关配置：server 块、proxy_pass、upstream 权重，负责路由转发，不负责 JWT 签发与校验配置。",
            "SSO 登录流程说明：CAS 与 OAuth2 的登录跳转与票据交换，属身份认证入口，与 user-service 的 JWT 配置不是一回事。",
            "MySQL 数据库连接配置：host/port/用户名密码，应用启动时加载，与鉴权无关。",
        ],
    },
    {
        "query": "Kubernetes Pod 一直处于 Pending 状态，怎么排查？",
        "relevant": [
            "Pod Pending 常见原因：节点资源不足（CPU/内存 request 无法满足）、存在 taint 且无对应 toleration、PVC 未绑定。先用 kubectl describe pod 查看 Events 定位。",
            "排查步骤：kubectl describe pod <name> 看 Scheduled 失败原因；kubectl describe node 查看可分配资源；为有污点节点添加 toleration 或移除 taint。",
            "Scheduler 无可用节点时 Events 会提示 0/N nodes are available: Insufficient cpu/memory；此时扩容节点或调小资源 request。",
        ],
        "irrelevant": [
            "Pod CrashLoopBackOff 处理：容器启动即退出、探针失败导致重启，属运行期问题，与 Pending（调度期）不同。",
            "Docker 镜像构建：Dockerfile 与多阶段构建优化，影响镜像体积与启动，与 Pod 调度无关。",
            "服务发现 DNS 配置：CoreDNS 与 /etc/resolv.conf，影响域名解析，不解决 Pending。",
        ],
    },
    {
        "query": "Nginx 返回 502 Bad Gateway 是什么原因？",
        "relevant": [
            "502 Bad Gateway：上游服务未启动或已崩溃、upstream 配置错误、连接超时。排查先确认后端进程健康，再看 error.log 中 connect() failed 与 upstream 地址。",
            "常见原因与修复：后端端口未监听 -> 启动服务；proxy_read_timeout 过短 -> 调大；upstream 配了不可达的 host -> 改为服务名或正确的内网地址。",
            "排障速查：curl 后端健康检查接口直连验证，若直连 200 而经 Nginx 502，多为 Nginx 与上游之间网络或超时问题。",
        ],
        "irrelevant": [
            "Nginx 499 Client Closed Request：客户端主动断开连接，通常因响应过慢客户端超时，与上游故障无关，需看后端耗时。",
            "MySQL 连接数打满：Too many connections 是数据库层问题，与 Nginx 网关返回的 502 无关。",
            "CDN 缓存命中率优化：静态资源缓存策略，不影响后端动态接口的 502。",
        ],
    },
    {
        "query": "内部服务间接口鉴权用 API Key 还是 JWT？",
        "relevant": [
            "内部服务间鉴权建议：轻量内网调用用 API Key（Header: X-API-Key，注册即用）；跨系统或面向外部调用用 JWT。详见 infra 团队《服务间鉴权规范 v2》。",
            "选型对比：API Key 实现简单、适合固定调用方；JWT 带过期与声明，适合需细粒度权限的调用方；规范要求密钥管理走统一 KMS，不外泄。",
            "接入要求：新服务默认注册 API Key 并限制来源 IP；敏感操作必须 JWT + 权限校验，二者不混用。",
        ],
        "irrelevant": [
            "前端登录页面实现：用户名密码表单与验证码，属用户侧 UI，与内部服务间鉴权无关。",
            "HTTPS 证书配置：TLS 证书申请与部署，解决传输加密，不解决身份鉴权。",
            "数据脱敏规则：日志与展示层对手机号、身份证脱敏，与接口鉴权方式无关。",
        ],
    },
    {
        "query": "MySQL 慢查询日志怎么开启？",
        "relevant": [
            "开启慢查询：my.cnf [mysqld] 设置 slow_query_log=ON、slow_query_log_file=/var/log/mysql/slow.log、long_query_time=1；也可 SET GLOBAL 动态开启，无需重启。",
            "配置说明：long_query_time 单位为秒，建议 1；日志按日期轮转，用 mysqldumpslow 汇总分析 TOP N 慢 SQL，配合 EXPLAIN 优化。",
            "排查慢接口：打开慢日志后定位执行计划中全表扫描的 SQL，结合索引优化，通常能解决大部分响应慢问题。",
        ],
        "irrelevant": [
            "MySQL 备份策略：逻辑备份与物理备份、binlog 增量，保障数据可恢复，与慢查询日志无关。",
            "MySQL 索引优化：选择合适的索引字段、避免隐式转换，是分析完慢日志之后的优化手段，而非开启日志本身。",
            "后端接口慢的排查：链路追踪与耗时分布定位是应用层手段，与数据库慢查询日志不直接相关。",
        ],
    },
    {
        "query": "Docker 容器磁盘空间满了怎么办？",
        "relevant": [
            "容器磁盘满排查：docker system df 查看镜像/容器/卷占用，清理悬空镜像 docker image prune、停止的容器 docker container prune、无主卷 docker volume prune，必要时 docker system prune -a。",
            "日志撑满磁盘是高频原因：运行容器加 --log-opt max-size=10m --log-opt max-file=3 限制日志大小，并清空已膨胀的 json 日志文件。",
            "预防措施：给 Docker data-root 单独挂盘并设告警，定期清理 CI 镜像；容器内写临时文件挂 tmpfs 避免落盘。",
        ],
        "irrelevant": [
            "Docker 网络模式：bridge/host/overlay 的选择与端口映射，影响网络通信，与磁盘空间无关。",
            "Kubernetes 存储类：StorageClass 与 PVC 动态供给，管理持久卷，属 K8s 存储，非 Docker 本地磁盘清理。",
            "宿主机内存不足处理：free 与 OOM 排查、swap 配置，解决的是内存问题而非磁盘空间。",
        ],
    },
]


# ---------------------------------------------------------------------------
# 3. 指标数学正确性单测（不依赖模型，始终运行）
# ---------------------------------------------------------------------------


class TestMetricMath(unittest.TestCase):
    def _hit(self, role: str) -> "Hit":
        from internal_kb_qa.core.hit import Hit

        return Hit(text=role, score=0.5, metadata={"eval_role": role})

    def test_mrr_first_relevant_at_top(self):
        ranked = [self._hit("relevant"), self._hit("irrelevant")]
        self.assertAlmostEqual(mrr_at_k(ranked, 3), 1.0)

    def test_mrr_second_position(self):
        ranked = [self._hit("irrelevant"), self._hit("relevant"), self._hit("irrelevant")]
        self.assertAlmostEqual(mrr_at_k(ranked, 3), 0.5)

    def test_mrr_none_relevant_in_k(self):
        ranked = [self._hit("irrelevant"), self._hit("irrelevant"), self._hit("relevant")]
        self.assertAlmostEqual(mrr_at_k(ranked, 2), 0.0)

    def test_recall_precision(self):
        ranked = [self._hit("relevant"), self._hit("irrelevant"), self._hit("relevant")]
        self.assertAlmostEqual(recall_at_k(ranked, 3, 2), 1.0)
        self.assertAlmostEqual(precision_at_k(ranked, 3), 2 / 3)
        self.assertAlmostEqual(recall_at_k(ranked, 3, 3), 2 / 3)

    def test_ndcg_perfect_is_one(self):
        ranked = [self._hit("relevant"), self._hit("relevant"), self._hit("relevant")]
        self.assertAlmostEqual(ndcg_at_k(ranked, 3, 3), 1.0)

    def test_ndcg_worse_than_perfect(self):
        ranked = [self._hit("irrelevant"), self._hit("relevant"), self._hit("relevant")]
        perfect = 1.0
        actual = ndcg_at_k(ranked, 3, 3)
        self.assertLess(actual, perfect)


# ---------------------------------------------------------------------------
# 4. Live 指标评估（真实模型，模型不可用时跳过）
# ---------------------------------------------------------------------------


class TestRerankLiveMetrics(unittest.TestCase):
    maxDiff = None

    def test_live_rerank_metrics(self):
        try:
            from internal_kb_qa.core.hit import Hit
            from internal_kb_qa.core.reranker import rerank
        except (OSError, ImportError, FileNotFoundError) as exc:
            self.skipTest(f"torch/模型环境不可用，跳过重排指标测试: {exc}")

        # 先触发一次模型加载，把加载耗时排除在指标之外
        import torch  # noqa: F401

        results = []
        for case in EVAL_SET:
            hits = [
                Hit(text=t, score=0.5, metadata={"eval_role": "relevant"})
                for t in case["relevant"]
            ] + [
                Hit(text=t, score=0.5, metadata={"eval_role": "irrelevant"})
                for t in case["irrelevant"]
            ]
            n_relevant = len(case["relevant"])
            try:
                ranked = rerank(case["query"], hits, top_k=TOP_K)
            except (OSError, ImportError, FileNotFoundError) as exc:
                self.skipTest(f"torch/模型环境不可用，跳过重排指标测试: {exc}")
            results.append(
                {
                    "query": case["query"],
                    "mrr": mrr_at_k(ranked, TOP_K),
                    "recall": recall_at_k(ranked, TOP_K, n_relevant),
                    "precision": precision_at_k(ranked, TOP_K),
                    "ndcg": ndcg_at_k(ranked, TOP_K, n_relevant),
                    "top1_role": ranked[0].metadata.get("eval_role") if ranked else "none",
                }
            )

        agg = {
            "MRR@3": sum(r["mrr"] for r in results) / len(results),
            "Recall@3": sum(r["recall"] for r in results) / len(results),
            "Precision@3": sum(r["precision"] for r in results) / len(results),
            "nDCG@3": sum(r["ndcg"] for r in results) / len(results),
        }

        print("\n===== 重排指标评测报告（bge-reranker-v2-m3, top_k=%d）=====" % TOP_K)
        print(f"{'#':>2}  {'MRR@3':>7} {'Rec@3':>7} {'Prec@3':>7} {'nDCG@3':>7}  top1相关  query")
        for i, r in enumerate(results, 1):
            mark = "Y" if r["top1_role"] == "relevant" else "N"
            print(
                f"{i:>2}  {r['mrr']:>7.3f} {r['recall']:>7.3f} "
                f"{r['precision']:>7.3f} {r['ndcg']:>7.3f}  {mark:>6}    {r['query'][:36]}"
            )
        print("-" * 100)
        for name, value in agg.items():
            target = THRESHOLDS[name]
            ok = "PASS" if value >= target else "FAIL"
            print(f"  {name}: {value:.4f}  (阈值 {target})  [{ok}]")
        print("=" * 100)

        # 断言
        for name, target in THRESHOLDS.items():
            with self.subTest(metric=name, threshold=target):
                self.assertGreaterEqual(agg[name], target, msg=f"{name} 未达阈值 {target}")


def run_summary() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestMetricMath, TestRerankLiveMetrics):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(
        f"总计: {result.testsRun}  通过: {result.testsRun - len(result.failures) - len(result.errors)}"
    )
    print(f"失败: {len(result.failures)}  错误: {len(result.errors)}  跳过: {len(result.skipped)}")
    print("=" * 60)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_summary())
