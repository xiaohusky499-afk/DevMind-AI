"""T11 压测脚本：Locust 性能测试。

验收口径（任务分工第 7 节）：50 并发 p95 延迟 < 5 秒。
"""
from locust import HttpUser, between, task


class QaUser(HttpUser):
    wait_time = between(0.5, 2)

    @task
    def health(self):
        self.client.get("/health")

    # TODO(T11 服务组): 增加 /api/create_session、/api/query、WS /api/stream 压测场景。
