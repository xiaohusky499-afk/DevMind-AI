"""T13 Advanced RAG 检索策略选择器（基线参考 + 企业内部技术知识库场景）。

在技术咨询分支内，根据用户问题选择四种检索增强策略之一：
    直接检索 / 假设问题检索(HyDE) / 子查询检索 / 回溯问题检索

"""
from __future__ import annotations

from langchain_core.prompts import PromptTemplate
from openai import OpenAI

from base.config import Config
from base.logger import logger


class StrategySelector:
    """LLM 驱动的 Advanced RAG 检索策略选择器。"""

    STRATEGY_DIRECT = "直接检索"
    STRATEGY_HYDE = "假设问题检索"
    STRATEGY_SUBQUERY = "子查询检索"
    STRATEGY_BACKTRACK = "回溯问题检索"

    ALL_STRATEGIES = (
        STRATEGY_BACKTRACK,
        STRATEGY_SUBQUERY,
        STRATEGY_HYDE,
        STRATEGY_DIRECT,
    )

    _instance: StrategySelector | None = None

    def __init__(self):
        conf = Config()
        self.client = OpenAI(
            api_key=conf.DASHSCOPE_API_KEY,
            base_url=conf.DASHSCOPE_BASE_URL,
        )
        self.llm_model = conf.LLM_MODEL
        self.strategy_prompt_template = self._get_strategy_prompt()

    @classmethod
    def get_instance(cls) -> StrategySelector:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _call_llm(self, prompt: str) -> str:
        try:
            completion = self.client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是企业内部技术知识库（Wiki/Runbook/API 文档/值班手册）的检索策略助手。"
                            "只返回策略名称，不要解释。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )
            return completion.choices[0].message.content if completion.choices else self.STRATEGY_DIRECT
        except Exception as e:
            logger.error(f"检索策略选择 LLM 调用失败: {e}")
            return self.STRATEGY_DIRECT

    def _get_strategy_prompt(self) -> PromptTemplate:
        return PromptTemplate(
            template="""
你是企业内部技术知识库的智能检索助手。知识库包含：Confluence/Wiki 技术文档、
Git 仓库 README/设计文档、OpenAPI 接口文档、值班 Runbook、故障复盘报告。

请分析用户查询：{query}

从以下四种检索增强策略中选择最适合的一种，**只返回策略名称**，不要解释。

1. **直接检索**
   - 适用：问题意图明确、含具体技术术语/配置项/接口路径/报错信息。
   - 示例：「订单服务 /api/v1/orders 鉴权方式是什么？」→ 直接检索
   - 示例：「测试环境 MySQL 连接池 max_connections 默认值？」→ 直接检索

2. **假设问题检索**
   - 适用：问题较抽象或口语化，直接检索召回差；需先构造假设性技术说明再检索。
   - 示例：「微服务怎么做全链路追踪和故障定位？」→ 假设问题检索
   - 示例：「缓存穿透一般怎么处理？」→ 假设问题检索

3. **子查询检索**
   - 适用：跨系统、多步骤、多实体联动，需拆成多个独立子问题分别检索。
   - 示例：「支付网关 502 怎么排查？on-call 升级流程是什么？」→ 子查询检索
   - 示例：「Redis 集群和单机在缓存雪崩场景下分别怎么处理？」→ 子查询检索

4. **回溯问题检索**
   - 适用：用户描述很长（故障现象+背景+环境），需简化为核心检索问句。
   - 示例：「生产 Pod CrashLoopBackOff，日志 OOM，节点 64G 内存，怎么处理？」→ 回溯问题检索

根据查询 {query}，直接返回策略名称（如 "直接检索"）。
""",
            input_variables=["query"],
        )

    def select_strategy(self, query: str) -> str:
        raw = self._call_llm(self.strategy_prompt_template.format(query=query)).strip()
        for name in self.ALL_STRATEGIES:
            if name in raw:
                strategy = name
                break
        else:
            strategy = self.STRATEGY_DIRECT

        logger.info(f"Advanced 检索策略: query='{query[:60]}' -> {strategy}")
        return strategy


if __name__ == "__main__":
    """T13 本地测试：Advanced 四策略选择（需 DASHSCOPE_API_KEY）。"""
    from base.config import Config

    if not Config().DASHSCOPE_API_KEY:
        print("跳过 live 测试：未配置 DASHSCOPE_API_KEY")
        raise SystemExit(0)

    selector = StrategySelector()
    cases = [
        ("支付服务调用网关 502 怎么排查，以及 on-call 升级流程？", StrategySelector.STRATEGY_SUBQUERY),
        ("user-service 的 JWT 鉴权配置在哪里？", StrategySelector.STRATEGY_DIRECT),
        ("微服务治理里熔断和限流一般怎么配合使用？", StrategySelector.STRATEGY_HYDE),
        ("生产环境 Pod CrashLoopBackOff，日志 OOM，节点 64G 内存，怎么处理？", StrategySelector.STRATEGY_BACKTRACK),
    ]
    for q, expected in cases:
        got = selector.select_strategy(q)
        mark = "OK" if got == expected else "?"
        print(f"[{mark}] 期望≈{expected}\n      实际={got}\n      query={q}\n")
    print("T13 strategy_selector: 本地测试完成")
