"""T4/T7 共享数据结构：检索命中结果。"""
from __future__ import annotations

from dataclasses import dataclass, field



@dataclass
class Hit:
    """混合检索 / 精排模块共用的召回结果。"""
    text: str
    score: float
    metadata: dict = field(default_factory=dict)
