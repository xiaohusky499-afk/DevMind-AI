"""T7 重排 + DashScope 流式生成回答。

主链路：
    T6 classify → 通用知识直接 LLM
    技术咨询 → T13 strategy_selector → query_rewrite → T4 hybrid_search → T7 rerank → 生成

对外接口：
    rag_answer(query, history, category) -> RAGResult
    rag_answer_stream(query, history, category) -> Generator[str]
"""
from __future__ import annotations

import time
from collections.abc import Generator
from dataclasses import dataclass, field

from openai import OpenAI

from base.config import Config
from base.logger import logger
from internal_kb_qa.core.confidence import evaluate_confidence, select_evidence
from internal_kb_qa.core.hit import Hit
from internal_kb_qa.core.intent_classifier import (
    CATEGORY_GENERAL,
    CATEGORY_TECH,
    classify,
)
from internal_kb_qa.core.prompts import RAGPrompts
from internal_kb_qa.core.query_rewrite import RewrittenQuery, query_rewrite
from internal_kb_qa.core.search_strategy import SearchStrategy, build_search_strategy


@dataclass
class Source:
    title: str
    page: int | None
    score: float
    text: str


@dataclass
class RAGResult:
    answer: str
    sources: list[Source] = field(default_factory=list)
    confidence: float = 0.0
    need_human: bool = False
    category: str = CATEGORY_TECH
    advanced_strategy: str = ""


def _format_history(history: list | None) -> str:
    if not history:
        return "（无）"
    lines = []
    for item in history[-5:]:
        if isinstance(item, dict) and "question" in item and "answer" in item:
            lines.append(f"Q: {item['question']}\nA: {item['answer']}")
    return "\n".join(lines) if lines else "（无）"


def _hits_to_sources(hits: list[Hit]) -> list[Source]:
    sources = []
    for hit in hits:
        meta = hit.metadata or {}
        sources.append(Source(
            title=meta.get("source", meta.get("title", "未知文档")),
            page=meta.get("page"),
            score=hit.score,
            text=hit.text[:300],
        ))
    return sources


def _build_context(hits: list[Hit]) -> str:
    blocks = []
    for i, hit in enumerate(hits, 1):
        meta = hit.metadata or {}
        title = meta.get("source", meta.get("title", f"文档{i}"))
        page = meta.get("page", "")
        page_str = f", 第{page}页" if page else ""
        blocks.append(f"[文档{i}: {title}{page_str}]\n{hit.text}")
    return "\n\n".join(blocks)


def _merge_hits(all_hits: list[Hit]) -> list[Hit]:
    """多路检索结果按文本去重，保留最高分。"""
    best: dict[str, Hit] = {}
    for hit in all_hits:
        if hit.text not in best or hit.score > best[hit.text].score:
            best[hit.text] = hit
    return sorted(best.values(), key=lambda x: x.score, reverse=True)


def _retrieve(
    search_queries: list[str],
    top_k: int,
    filters: dict,
) -> list[Hit]:
    """T4 混合检索：对多个 search_query 分别召回后合并去重。"""
    from internal_kb_qa.core.hybrid_search import search as hybrid_search

    if not search_queries:
        return []

    merged: list[Hit] = []
    for q in search_queries:
        try:
            hits = hybrid_search(q, top_k=top_k, filter=filters or None)
            merged.extend(hits)
        except Exception as e:
            logger.error(f"T4 检索失败 query='{q}': {e}")

    return _merge_hits(merged)


def _get_llm_client(conf: Config) -> OpenAI:
    return OpenAI(api_key=conf.DASHSCOPE_API_KEY, base_url=conf.DASHSCOPE_BASE_URL)


def _call_llm_stream(client: OpenAI, model: str, prompt: str) -> Generator[str, None, None]:
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是企业内部技术知识库智能助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        stream=True,
    )
    for chunk in completion:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def _call_llm_sync(client: OpenAI, model: str, prompt: str) -> str:
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是企业内部技术知识库智能助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    return completion.choices[0].message.content or ""


def _build_general_prompt(query: str, history: list | None) -> str:
    return RAGPrompts.rag_prompt().format(
        context="",
        history=_format_history(history),
        question=query,
    )


def _answer_general_knowledge(query: str, history: list | None, conf: Config) -> RAGResult:
    client = _get_llm_client(conf)
    answer = _call_llm_sync(client, conf.LLM_MODEL, _build_general_prompt(query, history))
    return RAGResult(
        answer=answer,
        sources=[],
        confidence=1.0,
        need_human=False,
        category=CATEGORY_GENERAL,
    )


def _run_rag_pipeline(
    query: str,
    history: list | None,
    category: str,
) -> tuple[list[Hit], RewrittenQuery | None, SearchStrategy | None]:
    """技术咨询：策略选择 → 改写 → 检索 → 精排。"""
    conf = Config()
    rewritten = query_rewrite(query, history, category)
    strategy = build_search_strategy(query, category)

    if strategy.strategy == "none" or not rewritten.search_queries:
        return [], rewritten, strategy

    hits = _retrieve(
        rewritten.search_queries,
        top_k=strategy.top_k,
        filters={**strategy.filters, **rewritten.filters},
    )

    if hits and strategy.use_rerank:
        from internal_kb_qa.core.reranker import rerank

        hits = rerank(query, hits, top_k=conf.RERANK_TOP_K)

    return select_evidence(hits), rewritten, strategy


def rag_answer(
    query: str,
    history: list | None = None,
    category: str | None = None,
) -> RAGResult:
    """非流式 RAG 主链路。"""
    conf = Config()
    start = time.time()

    if category is None:
        category = classify(query).category

    if category == CATEGORY_GENERAL:
        result = _answer_general_knowledge(query, history, conf)
        logger.info(f"通用知识回答完成, 耗时={time.time() - start:.2f}s")
        return result

    hits, rewritten, strategy = _run_rag_pipeline(query, history, category)
    advanced = rewritten.advanced_strategy if rewritten else ""

    if strategy and strategy.strategy == "none":
        return RAGResult(
            answer="该问题不在知识库服务范围内，建议转人工处理。",
            sources=[], confidence=0.0, need_human=True,
            category=category, advanced_strategy=advanced,
        )

    if not hits:
        return RAGResult(
            answer="根据当前知识库，未找到足够信息回答该问题，建议转人工处理。",
            sources=[], confidence=0.0, need_human=True,
            category=category, advanced_strategy=advanced,
        )

    confidence_result = evaluate_confidence(hits)
    confidence = confidence_result.score
    sources = _hits_to_sources(hits)

    if confidence_result.level == "low":
        return RAGResult(
            answer="根据当前知识库，未找到足够置信度的信息，建议转人工处理。",
            sources=sources, confidence=confidence, need_human=True,
            category=category, advanced_strategy=advanced,
        )

    context = _build_context(hits)
    prompt = RAGPrompts.rag_prompt().format(
        context=context,
        history=_format_history(history),
        question=query,
    )
    client = _get_llm_client(conf)
    answer = _call_llm_sync(client, conf.LLM_MODEL, prompt)

    logger.info(
        f"RAG 完成 strategy={advanced}, confidence={confidence:.4f}, "
        f"耗时={time.time() - start:.2f}s"
    )
    return RAGResult(
        answer=answer,
        sources=sources,
        confidence=confidence,
        need_human=False,
        category=category,
        advanced_strategy=advanced,
    )


def rag_answer_stream(
    query: str,
    history: list | None = None,
    category: str | None = None,
) -> Generator[str, None, None]:
    """流式 RAG（T8 WebSocket）。"""
    conf = Config()

    if category is None:
        category = classify(query).category

    if category == CATEGORY_GENERAL:
        client = _get_llm_client(conf)
        for token in _call_llm_stream(client, conf.LLM_MODEL, _build_general_prompt(query, history)):
            yield token
        return

    hits, _rewritten, strategy = _run_rag_pipeline(query, history, category)

    if strategy and strategy.strategy == "none":
        yield "该问题不在知识库服务范围内，建议转人工处理。"
        return

    if not hits:
        yield "根据当前知识库，未找到足够信息回答该问题，建议转人工处理。"
        return

    confidence_result = evaluate_confidence(hits)
    if confidence_result.level == "low":
        yield "根据当前知识库，未找到足够置信度的信息，建议转人工处理。"
        return

    context = _build_context(hits)
    prompt = RAGPrompts.rag_prompt().format(
        context=context,
        history=_format_history(history),
        question=query,
    )
    client = _get_llm_client(conf)
    for token in _call_llm_stream(client, conf.LLM_MODEL, prompt):
        yield token


def prepare_rag_context(
    query: str,
    history: list | None = None,
    category: str | None = None,
) -> tuple[list[Hit], list[Source], float, bool, str]:
    """流式生成前预检索，供 T8 返回 sources/confidence/advanced_strategy。"""
    if category is None:
        category = classify(query).category

    if category == CATEGORY_GENERAL:
        return [], [], 1.0, False, ""

    hits, rewritten, strategy = _run_rag_pipeline(query, history, category)
    advanced = rewritten.advanced_strategy if rewritten else ""

    if strategy and strategy.strategy == "none":
        return [], [], 0.0, True, advanced

    if not hits:
        return [], [], 0.0, True, advanced

    confidence_result = evaluate_confidence(hits)
    confidence = confidence_result.score
    need_human = confidence_result.level == "low"
    return hits, _hits_to_sources(hits), confidence, need_human, advanced


def _main() -> None:
    """T7 本地测试：RAG 主链路（T6→T13→T4→精排→生成）。

    运行（项目根目录）：
        python -m internal_kb_qa.core.rag_generator
    需要 DASHSCOPE_API_KEY；T4/T7 重依赖需 Milvus + torch 在线才能全链路跑通。
    """
    from base.config import Config

    if not Config().DASHSCOPE_API_KEY:
        print("跳过 live 测试：未配置 DASHSCOPE_API_KEY")
        return

    # 1) 通用知识：不检索
    general = rag_answer("什么是 Kubernetes？", category=CATEGORY_GENERAL)
    print(f"[通用知识] category={general.category} sources={len(general.sources)}")
    print(f"  answer: {general.answer[:120]}...")

    # 2) 技术咨询：全链路（T4/Milvus 或 torch 不可用时可能转人工兜底）
    tech = rag_answer("测试环境 MySQL 连接池默认 max_connections 是多少？")
    print(f"\n[技术咨询] category={tech.category} strategy={tech.advanced_strategy}")
    print(f"  confidence={tech.confidence:.4f} need_human={tech.need_human} sources={len(tech.sources)}")
    print(f"  answer: {tech.answer[:200]}...")

    print("\nT7 rag_generator: 本地测试完成（全链路需 Milvus + 精排模型在线）")


if __name__ == "__main__":
    _main()
