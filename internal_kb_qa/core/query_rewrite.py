"""T13 Query 改写 + 策略化检索 Query 构建。

对外接口：
    query_rewrite(query, history, category, advanced_strategy) -> RewrittenQuery
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from openai import OpenAI

from base.config import Config
from base.logger import logger
from internal_kb_qa.core.intent_classifier import CATEGORY_GENERAL, CATEGORY_TECH
from internal_kb_qa.core.prompts import RAGPrompts
from rag_qa.core.strategy_selector import StrategySelector

_COLLOQUIAL_MAP = {
    "挂了": "服务不可用",
    "崩了": "服务崩溃/OOM",
    "连不上": "连接失败",
    "超时": "请求超时/Timeout",
    "权限不够": "权限不足/鉴权失败",
    "登不上": "登录失败/认证失败",
}


@dataclass
class RewrittenQuery:
    rewritten_query: str
    sub_queries: list[str] = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    advanced_strategy: str = StrategySelector.STRATEGY_DIRECT
    search_queries: list[str] = field(default_factory=list)


def _format_history(history: list | None) -> str:
    if not history:
        return "（无）"
    lines = []
    for item in history[-5:]:
        if isinstance(item, dict) and "question" in item and "answer" in item:
            lines.append(f"Q: {item['question']}\nA: {item['answer']}")
    return "\n".join(lines) if lines else "（无）"


def _apply_colloquial_rules(query: str) -> str:
    result = query
    for colloquial, term in _COLLOQUIAL_MAP.items():
        if colloquial in result:
            result = result.replace(colloquial, term)
    return result


def _parse_llm_json(raw: str) -> dict:
    raw = raw.strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    return {}


def _call_llm_sync(prompt: str, system: str = "你是企业内部技术知识库助手。") -> str:
    conf = Config()
    client = OpenAI(api_key=conf.DASHSCOPE_API_KEY, base_url=conf.DASHSCOPE_BASE_URL)
    completion = client.chat.completions.create(
        model=conf.LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    return (completion.choices[0].message.content or "").strip()


def _generic_rewrite(query: str, history: list | None, category: str) -> tuple[str, list[str]]:
    """直接检索策略：通用 Query 改写（指代消解 + 口语转术语 + 子查询扩展）。"""
    rule_query = _apply_colloquial_rules(query)
    try:
        prompt = RAGPrompts.query_rewrite_prompt().format(
            query=rule_query,
            history=_format_history(history),
            category=category,
        )
        raw = _call_llm_sync(prompt, "你是 Query 改写助手，只输出 JSON。")
        parsed = _parse_llm_json(raw)
        rewritten = parsed.get("rewritten_query", rule_query).strip() or rule_query
        sub_queries = [q.strip() for q in parsed.get("sub_queries", []) if q and q.strip()]
        return rewritten, sub_queries[:4]
    except Exception as e:
        logger.error(f"通用 Query 改写失败: {e}")
        return rule_query, []


def _build_queries_by_strategy(
    query: str,
    history: list | None,
    category: str,
    advanced_strategy: str,
) -> tuple[str, list[str], list[str]]:
    """按 Advanced 策略生成用于混合检索的 query 列表。"""
    selector = StrategySelector

    if advanced_strategy == selector.STRATEGY_HYDE:
        hypo = _call_llm_sync(
            RAGPrompts.hyde_prompt().format(query=query),
            "你是企业内部技术文档作者，生成假设性技术说明用于检索。",
        )
        logger.info(f"HyDE 假设说明: {hypo[:100]}...")
        return hypo, [], [hypo]

    if advanced_strategy == selector.STRATEGY_SUBQUERY:
        sub_text = _call_llm_sync(
            RAGPrompts.subquery_prompt().format(query=query),
            "你是技术问题分解助手，每行输出一个子查询。",
        )
        sub_queries = [q.strip() for q in sub_text.split("\n") if q.strip()]
        logger.info(f"子查询检索: {sub_queries}")
        return query, sub_queries[:4], sub_queries[:4] or [query]

    if advanced_strategy == selector.STRATEGY_BACKTRACK:
        simplified = _call_llm_sync(
            RAGPrompts.backtracking_prompt().format(query=query),
            "你是技术问题简化助手，输出一句话核心检索问句。",
        )
        logger.info(f"回溯简化问句: {simplified}")
        return simplified, [], [simplified]

    # 直接检索：通用改写
    rewritten, sub_queries = _generic_rewrite(query, history, category)
    search_queries = [rewritten] + [q for q in sub_queries if q != rewritten]
    return rewritten, sub_queries, search_queries or [query]


def query_rewrite(
    query: str,
    history: list | None = None,
    category: str = CATEGORY_TECH,
    advanced_strategy: str | None = None,
) -> RewrittenQuery:
    """Query 改写：结合 T6 意图 + Advanced 检索策略，输出用于混合检索的 query 列表。"""
    if category == CATEGORY_GENERAL:
        return RewrittenQuery(rewritten_query=query, search_queries=[])

    if advanced_strategy is None:
        advanced_strategy = StrategySelector.get_instance().select_strategy(query)

    rewritten, sub_queries, search_queries = _build_queries_by_strategy(
        query, history, category, advanced_strategy,
    )

    logger.info(
        f"Query 改写完成: strategy={advanced_strategy}, "
        f"search_queries={search_queries}"
    )
    return RewrittenQuery(
        rewritten_query=rewritten,
        sub_queries=sub_queries,
        filters={},
        advanced_strategy=advanced_strategy,
        search_queries=search_queries,
    )


def _main() -> None:
    """T13 本地测试：Query 改写 + 策略化 search_queries 构建。

    运行（项目根目录）：
        python -m internal_kb_qa.core.query_rewrite
    需要 DASHSCOPE_API_KEY；会调用 LLM 做策略选择与改写。
    """
    from base.config import Config

    if not Config().DASHSCOPE_API_KEY:
        print("跳过 live 测试：未配置 DASHSCOPE_API_KEY")
        return

    cases = [
        ("Redis 挂了怎么办", CATEGORY_TECH, StrategySelector.STRATEGY_DIRECT),
        ("微服务怎么做链路追踪？", CATEGORY_TECH, StrategySelector.STRATEGY_HYDE),
    ]
    for query, category, strategy in cases:
        rw = query_rewrite(query, category=category, advanced_strategy=strategy)
        print(f"\nquery: {query}")
        print(f"  advanced_strategy: {rw.advanced_strategy}")
        print(f"  rewritten_query:   {rw.rewritten_query}")
        print(f"  search_queries:    {rw.search_queries}")

    rw_general = query_rewrite("什么是 Kubernetes？", category=CATEGORY_GENERAL)
    assert rw_general.search_queries == [], "通用知识不应产生检索 query"
    print("\n通用知识 search_queries=[] -> PASS")
    print("\nT13 query_rewrite: 本地测试完成")


if __name__ == "__main__":
    _main()
