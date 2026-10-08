"""T13 检索策略路由。

对外接口：
    build_search_strategy(query: str, category: str) -> SearchStrategy
策略：hybrid / none（与 T6 两类意图对齐）
"""
from __future__ import annotations

from dataclasses import dataclass, field

from base.config import Config
from internal_kb_qa.core.intent_classifier import CATEGORY_GENERAL, CATEGORY_TECH


@dataclass
class SearchStrategy:
    strategy: str          # hybrid / none
    top_k: int
    use_rerank: bool
    filters: dict = field(default_factory=dict)


_CATEGORY_STRATEGY = {
    CATEGORY_TECH: "hybrid",
    CATEGORY_GENERAL: "none",
}


def build_search_strategy(query: str, category: str) -> SearchStrategy:
    """根据 T6 意图分类结果，构建检索策略。"""
    conf = Config()
    strategy = _CATEGORY_STRATEGY.get(category, "hybrid")
    use_rerank = strategy == "hybrid"

    return SearchStrategy(
        strategy=strategy,
        top_k=conf.TOP_K,
        use_rerank=use_rerank,
        filters={},
    )


def _main() -> None:
    """T13 本地测试：检索策略路由（无需 LLM / Milvus / 模型）。"""
    cases = [
        ("MySQL 连接池默认配置是多少？", CATEGORY_TECH),
        ("今天天气怎么样？", CATEGORY_GENERAL),
    ]
    passed = 0
    for query, category in cases:
        s = build_search_strategy(query, category)
        ok = (category == CATEGORY_TECH and s.strategy == "hybrid" and s.use_rerank) or (
            category == CATEGORY_GENERAL and s.strategy == "none" and not s.use_rerank
        )
        passed += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] category={category!r} -> {s}")
    print(f"\nT13 search_strategy: {passed}/{len(cases)} 通过")


if __name__ == "__main__":
    _main()
