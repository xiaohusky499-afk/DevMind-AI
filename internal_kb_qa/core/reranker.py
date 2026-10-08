"""T7 bge-reranker-v2-m3 重排。

对 T4 召回结果重排，输出 top-k 带分数片段。
"""
# TODO(T7 算法组): 对 T4 召回结果重排，输出 top-k 带分数片段。

from __future__ import annotations

from pathlib import Path
from threading import Lock

import torch
from FlagEmbedding import FlagReranker

from base.config import Config
from base.logger import logger
from internal_kb_qa.core.hit import Hit

# transformers>=5 移除了 tokenizer.prepare_for_model，FlagEmbedding 1.4.0 仍依赖它。
# 这里在加载后给 tokenizer 实例补一个等价实现（bos+query+eos+passage+eos，only_second 截断）。
_BOS_ID, _EOS_ID = 0, 2


def _compat_prepare_for_model(ids, pair_ids=None, truncation=None, max_length=None,
                              padding=False, add_special_tokens=True, **kwargs):
    ids = list(ids)
    if pair_ids is None:
        seq = [_BOS_ID] + ids + [_EOS_ID]
        if truncation and max_length and len(seq) > max_length:
            seq = seq[:max_length]
    else:
        pair_ids = list(pair_ids)
        keep_first = 1 + len(ids) + 1  # bos + ids + eos
        if truncation == "only_second" and max_length:
            max_second = max_length - keep_first - 1  # 预留结尾 eos
            if len(pair_ids) > max_second:
                pair_ids = pair_ids[:max_second]
        seq = [_BOS_ID] + ids + [_EOS_ID] + pair_ids + [_EOS_ID]
        if truncation and truncation != "only_second" and max_length and len(seq) > max_length:
            seq = seq[:max_length]
    return {"input_ids": seq, "attention_mask": [1] * len(seq)}


def _patch_tokenizer_prepare_for_model(model: FlagReranker) -> None:
    """transformers>=5 兼容：FlagEmbedding 内部仍调用 tokenizer.prepare_for_model。"""
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is not None and not hasattr(tokenizer, "prepare_for_model"):
        tokenizer.prepare_for_model = _compat_prepare_for_model
        logger.info("已为精排 tokenizer 注入 prepare_for_model 兼容实现（transformers>=5）")


def _get_reranker_model_path() -> str:
    """模型路径：internal_kb_qa/models/bge-reranker-v2-m3"""
    model_dir = Path(__file__).resolve().parents[1] / "models" / "bge-reranker-v2-m3"
    if not model_dir.exists():
        raise FileNotFoundError(
            f"精排模型未找到: {model_dir}\n"
            f"请从参考项目复制 bge-reranker-v2-m3 到 internal_kb_qa/models/"
        )
    required_files = ("config.json", "model.safetensors", "tokenizer.json", "sentencepiece.bpe.model")
    missing = [name for name in required_files if not (model_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"精排模型文件不完整: {model_dir}\n"
            f"缺少文件: {missing}\n"
            f"请运行: python -m internal_kb_qa.scripts.download_models bge-reranker-v2-m3"
        )
    return str(model_dir)


class Reranker:
    """bge-reranker-v2-m3 精排器（单例懒加载）。"""

    _instance: Reranker | None = None
    _instance_lock = Lock()

    def __init__(self):
        conf = Config()
        self.top_k = conf.RERANK_TOP_K
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        model_path = _get_reranker_model_path()
        logger.info(f"加载精排模型: {model_path}, device={self.device}")
        self._model = FlagReranker(
            model_path,
            use_fp16=(self.device == "cuda"),
        )
        self._inference_lock = Lock()
        _patch_tokenizer_prepare_for_model(self._model)

    @classmethod
    def get_instance(cls) -> Reranker:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def rerank(
        self,
        query: str,
        hits: list[Hit],
        top_k: int | None = None,
    ) -> list[Hit]:
        """对召回结果精排，返回 top-k 带新分数的 Hit 列表。"""
        if not hits:
            return []

        top_k = top_k or self.top_k
        if len(hits) == 1:
            return hits[:top_k]

        pairs = [[query, hit.text] for hit in hits]
        with self._inference_lock:
            scores = self._model.compute_score(pairs, normalize=True)

        if isinstance(scores, (int, float)):
            scores = [scores]

        ranked = sorted(zip(scores, hits), key=lambda x: x[0], reverse=True)
        result = []
        for score, hit in ranked[:top_k]:
            result.append(Hit(text=hit.text, score=float(score), metadata=hit.metadata))
        logger.info(f"精排完成: {len(hits)} -> {len(result)}, top1_score={result[0].score:.4f}")
        return result


def rerank(query: str, hits: list[Hit], top_k: int | None = None) -> list[Hit]:
    """模块级便捷函数。"""
    return Reranker.get_instance().rerank(query, hits, top_k)


def _main() -> None:
    """T7 本地测试：bge-reranker-v2-m3 精排。

    运行（项目根目录）：
        python -m internal_kb_qa.core.reranker
    需要 internal_kb_qa/models/bge-reranker-v2-m3 且 torch 可用。
    """
    query = "MySQL 连接池 max_connections 默认配置"
    hits = [
        Hit(text="Redis 集群主从切换步骤...", score=0.5, metadata={"source": "runbook-redis.md"}),
        Hit(text="MySQL 连接池 max_connections 默认值为 151...", score=0.4, metadata={"source": "wiki-mysql.md"}),
    ]
    try:
        ranked = rerank(query, hits, top_k=1)
    except OSError as exc:
        print(f"跳过 live 测试：torch/模型环境不可用 ({exc})")
        return
    except FileNotFoundError as exc:
        print(f"跳过 live 测试：{exc}")
        return

    print(f"query: {query}")
    for i, h in enumerate(ranked, 1):
        print(f"  [{i}] score={h.score:.4f} source={h.metadata.get('source')} text={h.text[:60]}...")
    top_is_mysql = ranked and "MySQL" in ranked[0].text
    print(f"\nT7 reranker: top1 与 MySQL 相关 -> {'PASS' if top_is_mysql else 'FAIL'}")


if __name__ == "__main__":
    _main()
