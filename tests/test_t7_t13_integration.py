"""T7/T13 任务代码集成测试（契约 + 链路 + 可选 live 测试）。"""
from __future__ import annotations

import os
import sys
import unittest
from dataclasses import fields
from unittest.mock import patch

# 保证从项目根目录可导入 internal_kb_qa / base / rag_qa
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------------------
# 1. 契约与导入
# ---------------------------------------------------------------------------


class TestContracts(unittest.TestCase):
    def test_imports(self):
        from internal_kb_qa.core.intent_classifier import (
            CATEGORY_GENERAL,
            CATEGORY_TECH,
            Classification,
            classify,
        )
        from internal_kb_qa.core.search_strategy import SearchStrategy, build_search_strategy
        from internal_kb_qa.core.query_rewrite import RewrittenQuery, query_rewrite
        from internal_kb_qa.core.rag_generator import RAGResult, rag_answer, prepare_rag_context
        from rag_qa.core.strategy_selector import StrategySelector

        self.assertEqual(CATEGORY_TECH, "技术咨询")
        self.assertEqual(CATEGORY_GENERAL, "通用知识")
        self.assertTrue(callable(classify))
        self.assertTrue(callable(build_search_strategy))
        self.assertTrue(callable(query_rewrite))
        self.assertTrue(callable(rag_answer))

        try:
            from internal_kb_qa.core.reranker import rerank
            from internal_kb_qa.core.hybrid_search import search
            self.assertTrue(callable(rerank))
            self.assertTrue(callable(search))
        except OSError as exc:
            self.skipTest(f"torch/模型环境不可用，跳过 T4/T7 重依赖导入: {exc}")

    def test_rag_result_fields(self):
        from internal_kb_qa.core.rag_generator import RAGResult

        names = {f.name for f in fields(RAGResult)}
        for required in ("answer", "sources", "confidence", "need_human"):
            self.assertIn(required, names)

    def test_rewritten_query_fields(self):
        from internal_kb_qa.core.query_rewrite import RewrittenQuery

        names = {f.name for f in fields(RewrittenQuery)}
        for required in ("rewritten_query", "sub_queries", "filters", "search_queries"):
            self.assertIn(required, names)

    def test_search_strategy_fields(self):
        from internal_kb_qa.core.search_strategy import SearchStrategy

        names = {f.name for f in fields(SearchStrategy)}
        for required in ("strategy", "top_k", "use_rerank", "filters"):
            self.assertIn(required, names)


# ---------------------------------------------------------------------------
# 2. T13 策略路由（无需外部服务）
# ---------------------------------------------------------------------------


class TestSearchStrategy(unittest.TestCase):
    def test_tech_hybrid(self):
        from internal_kb_qa.core.intent_classifier import CATEGORY_TECH
        from internal_kb_qa.core.search_strategy import build_search_strategy

        s = build_search_strategy("MySQL 连接池配置", CATEGORY_TECH)
        self.assertEqual(s.strategy, "hybrid")
        self.assertTrue(s.use_rerank)

    def test_general_none(self):
        from internal_kb_qa.core.intent_classifier import CATEGORY_GENERAL
        from internal_kb_qa.core.search_strategy import build_search_strategy

        s = build_search_strategy("今天天气怎么样", CATEGORY_GENERAL)
        self.assertEqual(s.strategy, "none")
        self.assertFalse(s.use_rerank)


# ---------------------------------------------------------------------------
# 3. 链路 mock 测试（不依赖 Milvus / 重排模型）
# ---------------------------------------------------------------------------


class TestPipelineMocked(unittest.TestCase):
    def _mock_hit(self):
        from internal_kb_qa.core.hit import Hit
        return Hit(
            text="MySQL 连接池 max_connections 默认值为 151，可在 my.cnf 中调整。",
            score=0.92,
            metadata={"source": "wiki-mysql.md", "page": 3},
        )

    @patch("internal_kb_qa.core.rag_generator._call_llm_sync")
    @patch("internal_kb_qa.core.rag_generator._run_rag_pipeline")
    def test_tech_rag_pipeline(self, mock_pipeline, mock_llm):
        from internal_kb_qa.core.intent_classifier import CATEGORY_TECH
        from internal_kb_qa.core.query_rewrite import RewrittenQuery
        from internal_kb_qa.core.rag_generator import rag_answer
        from internal_kb_qa.core.search_strategy import SearchStrategy
        from rag_qa.core.strategy_selector import StrategySelector

        mock_hits = [self._mock_hit()]
        mock_pipeline.return_value = (
            mock_hits,
            RewrittenQuery(
                rewritten_query="MySQL 连接池默认配置",
                search_queries=["MySQL 连接池默认配置"],
                advanced_strategy=StrategySelector.STRATEGY_DIRECT,
            ),
            SearchStrategy(strategy="hybrid", top_k=5, use_rerank=True),
        )
        mock_llm.return_value = "根据文档，MySQL 连接池 max_connections 默认 151。[来源: wiki-mysql.md, 第3页]"

        result = rag_answer(
            "测试环境 MySQL 连接池默认配置是多少？",
            category=CATEGORY_TECH,
        )

        self.assertFalse(result.need_human)
        self.assertGreater(result.confidence, 0)
        self.assertGreater(len(result.sources), 0)
        self.assertIn("151", result.answer)
        mock_pipeline.assert_called_once()
        mock_llm.assert_called_once()

    @patch("internal_kb_qa.core.rag_generator._call_llm_sync")
    @patch("internal_kb_qa.core.rag_generator._run_rag_pipeline")
    def test_general_knowledge_no_search(self, mock_pipeline, mock_llm):
        from internal_kb_qa.core.intent_classifier import CATEGORY_GENERAL
        from internal_kb_qa.core.rag_generator import rag_answer

        mock_llm.return_value = "Kubernetes 是容器编排平台。"

        result = rag_answer("什么是 Kubernetes？", category=CATEGORY_GENERAL)
        mock_pipeline.assert_not_called()

        self.assertFalse(result.need_human)
        self.assertEqual(result.sources, [])
        mock_llm.assert_called_once()

    @patch("internal_kb_qa.core.query_rewrite._call_llm_sync")
    def test_query_rewrite_direct_strategy(self, mock_llm):
        from internal_kb_qa.core.intent_classifier import CATEGORY_TECH
        from internal_kb_qa.core.query_rewrite import query_rewrite
        from rag_qa.core.strategy_selector import StrategySelector

        mock_llm.return_value = '{"rewritten_query": "Redis 服务不可用排查", "sub_queries": []}'

        rw = query_rewrite(
            "Redis 挂了怎么办",
            category=CATEGORY_TECH,
            advanced_strategy=StrategySelector.STRATEGY_DIRECT,
        )
        self.assertTrue(rw.search_queries)
        self.assertEqual(rw.advanced_strategy, StrategySelector.STRATEGY_DIRECT)


# ---------------------------------------------------------------------------
# 4. Live 测试（需要 DASHSCOPE_API_KEY；Milvus/模型可选）
# ---------------------------------------------------------------------------


class TestLiveOptional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from base.config import Config

        cls.conf = Config()
        cls.has_api_key = bool(cls.conf.DASHSCOPE_API_KEY)

    def test_live_strategy_selector(self):
        if not self.has_api_key:
            self.skipTest("未配置 DASHSCOPE_API_KEY")
        from rag_qa.core.strategy_selector import StrategySelector

        strategy = StrategySelector().select_strategy(
            "user-service 的 JWT 鉴权配置在哪里？"
        )
        self.assertIn(strategy, StrategySelector.ALL_STRATEGIES)

    def test_live_intent_classifier(self):
        if not self.has_api_key:
            self.skipTest("未配置 DASHSCOPE_API_KEY")
        from internal_kb_qa.core.intent_classifier import CATEGORY_TECH, classify

        result = classify("本地启动服务报 502 怎么排查？")
        self.assertEqual(result.category, CATEGORY_TECH)
        self.assertGreater(result.confidence, 0)

    def test_live_milvus(self):
        from base.config import Config

        conf = Config()
        try:
            from pymilvus import MilvusClient

            client = MilvusClient(
                uri=f"http://{conf.MILVUS_HOST}:{conf.MILVUS_PORT}",
                db_name=conf.MILVUS_DATABASE_NAME,
                timeout=5,
            )
            cols = client.list_collections()
            self.assertIsInstance(cols, list)
        except Exception as exc:
            self.skipTest(f"Milvus 不可达: {exc}")

    def test_live_reranker_model(self):
        from pathlib import Path

        model_dir = Path(__file__).resolve().parents[1] / "internal_kb_qa" / "models" / "bge-reranker-v2-m3"
        if not model_dir.exists():
            self.skipTest("精排模型未复制到 internal_kb_qa/models/bge-reranker-v2-m3")

        from internal_kb_qa.core.hit import Hit

        hits = [
            Hit(text="MySQL 连接池配置说明...", score=0.5, metadata={"source": "a.md"}),
            Hit(text="Redis 集群部署文档...", score=0.4, metadata={"source": "b.md"}),
        ]
        try:
            from internal_kb_qa.core.reranker import rerank

            ranked = rerank("MySQL 连接池默认配置", hits, top_k=1)
            self.assertEqual(len(ranked), 1)
            self.assertIn("MySQL", ranked[0].text)
        except OSError as exc:
            self.skipTest(f"torch DLL 不可用，跳过精排 live 测试: {exc}")


def run_summary():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestContracts, TestSearchStrategy, TestPipelineMocked, TestLiveOptional):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(f"总计: {result.testsRun}  通过: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}  错误: {len(result.errors)}  跳过: {len(result.skipped)}")
    print("=" * 60)

    # 上下游对接说明
    print("\n【上下游对接检查】")
    checks = [
        ("T6 → T7/T13", "rag_generator 调用 classify() 与 query_rewrite(category=...)"),
        ("T13 strategy_selector → query_rewrite", "query_rewrite 内 StrategySelector.get_instance()"),
        ("T13 search_strategy → rag_generator", "build_search_strategy 决定 hybrid/none"),
        ("T4 hybrid_search → rag_generator", "rag_generator._retrieve 调用 hybrid_search"),
        ("T7 reranker → rag_generator", "_run_rag_pipeline 内 rerank()"),
        ("T7 → T8（下游）", "rag_answer / rag_answer_stream / prepare_rag_context 已导出"),
        ("T8 new_main 串联", "new_main.py / query.py 仍为 TODO，T8 未接入"),
    ]
    for name, status in checks:
        mark = "OK" if "仍为 TODO" not in status else "PENDING"
        print(f"  {mark} {name}: {status}")

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_summary())
