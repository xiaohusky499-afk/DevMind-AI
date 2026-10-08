"""T3 文档切分器。

对外接口（任务分工 T3 契约）：
    split(documents: list[Document]) -> list[Chunk]
"""
from .code_splitter import CodeSplitter
from .recursive_splitter import RecursiveSplitter

__all__ = ["split", "RecursiveSplitter", "CodeSplitter"]


def split(documents):
    raise NotImplementedError("TODO(T3 算法组): implement split()")
