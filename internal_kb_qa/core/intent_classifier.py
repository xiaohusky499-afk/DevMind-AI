"""T6 意图分类与路由（LLM + Prompt few-shot，不再训练 BERT）。

对外接口：
    classify(query: str) -> Classification

分类类别（在原五类 tech / access_request / incident / complaint / chitchat 基础上
简化为两类，见 docs/企业内部技术知识库智能问答系统-任务分工.md T6）：
    - 技术咨询：环境安装、报错排查、接口调试、代码实现、架构配置、系统故障等技术咨询类问题
    - 通用知识：概念科普、行业资讯、公司制度、生活常识、寒暄闲聊等非技术实操类问题

路由约定：
    - 置信度低于 LOW_CONFIDENCE_THRESHOLD 时一律按「技术咨询」保守路由到带引用的 RAG 主链路；
    - LLM 调用失败、超时或返回格式非法时降级返回「技术咨询」并记录日志，保证主链路不中断。
"""

from __future__ import annotations

import json
import logging
import os
import re

from pydantic import BaseModel, Field

try:  # 项目根通用日志（base/logger.py，输出到 stdout）
    from base.logger import logger
except ImportError:  # 独立运行时兜底，避免无 handler 导致日志不可见
    logger = logging.getLogger("devmind-ai")

try:  # 项目根配置（base/config.py，读取 config.ini 的 [llm] 段）
    from base.config import Config
except ImportError:
    Config = None

try:  # DashScope 走 OpenAI 兼容协议，依赖 openai SDK
    from openai import OpenAI
except ImportError:
    OpenAI = None

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------

# 分类类别（对外契约取值）
CATEGORY_TECH = "技术咨询"
CATEGORY_GENERAL = "通用知识"
VALID_CATEGORIES = frozenset({CATEGORY_TECH, CATEGORY_GENERAL})

# 低置信度保守路由阈值：confidence < 阈值时按「技术咨询」处理（T6 文档约定）
LOW_CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "0.6"))

# LLM 调用参数
LLM_TIMEOUT_SECONDS = float(os.getenv("INTENT_LLM_TIMEOUT", "10"))  # 单次调用超时（秒）
LLM_MAX_TOKENS = int(os.getenv("INTENT_LLM_MAX_TOKENS", "200"))
LLM_TEMPERATURE = 0.0  # 分类任务要求确定性输出
DEFAULT_MODEL = "qwen3-max"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# ---------------------------------------------------------------------------
# T6 few-shot Prompt 模板
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是企业内部技术知识库的意图分类助手。
你的任务：判断用户问题属于「技术咨询」还是「通用知识」，供问答系统主链路路由。

分类定义：
- 「技术咨询」：与研发/运维/测试等技术实操直接相关，例如环境安装部署、报错排查、
  系统故障处理、接口调试、代码实现、架构与配置调整、性能优化、权限申请等。
- 「通用知识」：不涉及技术实操的知识性/闲聊类问题，例如概念科普（"什么是……"）、
  行业资讯、公司制度与福利、生活常识、寒暄闲聊等。

边界规则：
- 出现具体技术栈、报错信息、配置项、代码、接口异常、系统故障的 -> 「技术咨询」；
- 仅做概念解释、原理科普，不涉及具体技术操作动作的 -> 「通用知识」；
- 拿不准时优先判为「技术咨询」（宁可多查知识库，不直接闲聊打发）。

输出要求：
- 只输出一个 JSON 对象，字段为 category、confidence；
- category 取值仅限 "技术咨询" 或 "通用知识"；
- confidence 为 0 到 1 的浮点数，表示分类置信度；
- 不要输出 JSON 之外的任何文字、解释或 Markdown 代码块。"""

# few-shot 示例（覆盖两类，assistant 侧输出为合法 JSON）
FEW_SHOT_EXAMPLES: tuple[tuple[str, dict], ...] = (
    (
        "本地启动 Spring Boot 服务报 Error creating bean with name 'dataSource'，怎么排查？",
        {"category": CATEGORY_TECH, "confidence": 0.97},
    ),
    (
        "生产环境 Kafka 消费积压突然升高，有哪些排查步骤？",
        {"category": CATEGORY_TECH, "confidence": 0.95},
    ),
    (
        "如何把公司内网 Maven 私服地址配置到 settings.xml？",
        {"category": CATEGORY_TECH, "confidence": 0.93},
    ),
    (
        "调用订单接口一直返回 503 Service Unavailable，调用方该怎么处理？",
        {"category": CATEGORY_TECH, "confidence": 0.96},
    ),
    (
        "git push 提示 Permission denied (publickey)，怎么解决？",
        {"category": CATEGORY_TECH, "confidence": 0.96},
    ),
    (
        "什么是微服务架构，它和单体架构的区别是什么？",
        {"category": CATEGORY_GENERAL, "confidence": 0.94},
    ),
    (
        "公司年假一共有多少天，怎么申请？",
        {"category": CATEGORY_GENERAL, "confidence": 0.96},
    ),
    (
        "今天天气怎么样？",
        {"category": CATEGORY_GENERAL, "confidence": 0.98},
    ),
)

# ---------------------------------------------------------------------------
# 出参模型
# ---------------------------------------------------------------------------


class Classification(BaseModel):
    """T6 意图分类结果（对外契约）。"""

    category: str = Field(..., description=f"意图类别：{CATEGORY_TECH} / {CATEGORY_GENERAL}")
    confidence: float = Field(..., ge=0.0, le=1.0, description="分类置信度，0~1")


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def classify(query: str) -> Classification:
    """对用户问题做「技术咨询 / 通用知识」二分类，供主链路路由。

    处理流程：
    1. 空 query 直接降级；
    2. 组装 few-shot Prompt 调用 LLM，要求仅输出 JSON；
    3. 解析并校验 LLM 返回（容忍 Markdown 代码块围栏与前后附加文字）；
    4. 置信度低于 LOW_CONFIDENCE_THRESHOLD 时按「技术咨询」保守路由；
    5. LLM 失败 / 超时 / 返回格式非法时降级返回「技术咨询」并记录日志，主链路不中断。

    Args:
        query: 用户问题。

    Returns:
        Classification: 分类结果（category、confidence）。
    """
    if not query or not query.strip():
        return _fallback("query 为空")

    messages = _build_few_shot_messages(query)

    try:
        raw = _call_llm(messages)
    except Exception as exc:  # 降级策略要求吞掉一切 LLM 异常，不向主链路抛出
        logger.error("intent.classify.llm_error query=%r error=%s", query, exc, exc_info=True)
        return _fallback(f"LLM 调用失败：{exc}")

    parsed = _parse_llm_json(raw)
    if parsed is None:
        logger.warning("intent.classify.invalid_format query=%r raw=%r", query, raw)
        return _fallback("LLM 返回格式非法")

    category, confidence = parsed

    if confidence < LOW_CONFIDENCE_THRESHOLD:
        logger.info(
            "intent.classify.low_confidence query=%r confidence=%.4f threshold=%.2f -> 保守路由为%s",
            query, confidence, LOW_CONFIDENCE_THRESHOLD, CATEGORY_TECH,
        )
        return Classification(category=CATEGORY_TECH, confidence=round(confidence, 4))

    logger.info("intent.classify.done query=%r category=%r confidence=%.4f", query, category, confidence)
    return Classification(category=category, confidence=round(confidence, 4))


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------

_llm_client: OpenAI | None = None
_llm_client_checked: bool = False
_llm_model: str = DEFAULT_MODEL

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _build_few_shot_messages(query: str) -> list[dict[str, str]]:
    """组装 system + few-shot 对话对 + 当前 query 的消息列表。"""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for example_query, example_result in FEW_SHOT_EXAMPLES:
        messages.append({"role": "user", "content": example_query})
        messages.append({"role": "assistant", "content": json.dumps(example_result, ensure_ascii=False)})
    messages.append({"role": "user", "content": query})
    return messages


def _load_llm_settings() -> tuple[str, str, str]:
    """读取 LLM 接入配置，环境变量优先，其次 config.ini 的 [llm] 段。"""
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL)
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)

    cfg = None
    if Config is not None:
        # 按文件位置定位项目根 config.ini，避免依赖运行 cwd
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(project_root, "config.ini")
        try:
            cfg = Config(config_file=config_path)
        except Exception:
            logger.warning("intent.classify.config_load_failed path=%s", config_path, exc_info=True)
    if cfg is not None:
        api_key = api_key or cfg.DASHSCOPE_API_KEY
        base_url = cfg.DASHSCOPE_BASE_URL or base_url
        model = cfg.LLM_MODEL or model
    return api_key, base_url, model


def _get_client() -> OpenAI | None:
    """懒加载 OpenAI 兼容客户端（进程内单例），不可用时返回 None。"""
    global _llm_client, _llm_client_checked, _llm_model
    if _llm_client_checked:
        return _llm_client
    _llm_client_checked = True

    api_key, base_url, model = _load_llm_settings()
    _llm_model = model
    if OpenAI is None:
        logger.error("intent.classify.no_openai_pkg: openai 包未安装，意图分类降级为技术咨询")
        return None
    if not api_key:
        logger.error("intent.classify.no_api_key: DASHSCOPE_API_KEY 未配置，意图分类降级为技术咨询")
        return None
    try:
        _llm_client = OpenAI(api_key=api_key, base_url=base_url, timeout=LLM_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.error("intent.classify.init_client_failed error=%s", exc, exc_info=True)
        _llm_client = None
    return _llm_client


def _call_llm(messages: list[dict[str, str]]) -> str:
    """调用 LLM 完成分类，返回原始文本；失败 / 超时由调用方统一降级。"""
    client = _get_client()
    if client is None:
        raise RuntimeError("LLM 客户端不可用（api_key 缺失或 openai SDK 未安装）")
    resp = client.chat.completions.create(
        model=_llm_model,
        messages=messages,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    )
    content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    if not content:
        raise RuntimeError("LLM 返回内容为空")
    return content


def _normalize_category(value: object) -> str | None:
    """把 LLM 可能返回的别名（如"技术问题"、"general"）归一为两类契约取值。"""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text == "技术咨询" or text.startswith("技术"):
        return CATEGORY_TECH
    if "tech" in text:
        return CATEGORY_TECH
    if "通用" in text or "知识" in text or "闲聊" in text or "其他" in text:
        return CATEGORY_GENERAL
    if "general" in text or "chitchat" in text:
        return CATEGORY_GENERAL
    return None


def _parse_llm_json(raw: str) -> tuple[str, float] | None:
    """解析 LLM 返回的 JSON，容忍代码块围栏与前后附加文字；非法时返回 None。"""
    text = (raw or "").strip()
    if not text:
        return None

    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()

    # 容忍 JSON 前后附带的说明文字：截取首尾大括号之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    category = _normalize_category(data.get("category"))
    if category is None:
        return None

    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        return None
    confidence = min(max(confidence, 0.0), 1.0)

    return category, confidence


def _fallback(reason: str) -> Classification:
    """降级结果：一律按「技术咨询」保守路由，confidence=0 提示下游信息不足。"""
    full_reason = f"降级为技术咨询（{reason}）"
    logger.warning("intent.classify.fallback: %s", full_reason)
    return Classification(category=CATEGORY_TECH, confidence=0.0)


# ---------------------------------------------------------------------------
# 本地测试入口
# ---------------------------------------------------------------------------


def main() -> None:
    """T6 本地测试：对一组两类 query 分类并与预期比对。

    运行方式（在项目根目录、激活 .venv 后）：
        python -m internal_kb_qa.core.intent_classifier
    或：
        python internal_kb_qa/core/intent_classifier.py

    需要已配置 DASHSCOPE_API_KEY（环境变量或 config.ini [llm] 段），否则全部走降级路径。
    """
    test_cases: list[tuple[str, str]] = [
        # ---- 预期：技术咨询 -------
        ("打包部署到测试环境时页面 502，nginx 日志显示 upstream timed out，怎么排查？", CATEGORY_TECH),
        ("用 uv 给 Python 项目创建虚拟环境并从 requirements.txt 安装依赖，完整步骤是什么？", CATEGORY_TECH),
        ("MySQL 主从同步延迟突然变大，有哪些排查思路？", CATEGORY_TECH),
        ("Java 里 ConcurrentHashMap 和 HashMap 在并发场景下应该怎么选？", CATEGORY_TECH),
        ("WebSocket 连接升级时返回 401 未授权，前端怎么带上 SSO token？", CATEGORY_TECH),
        # ---- 预期：通用知识 -------
        ("什么是 Kubernetes，它主要解决什么问题？", CATEGORY_GENERAL),
        ("公司报销流程是什么，发票应该怎么贴？", CATEGORY_GENERAL),
        ("程序员久坐腰疼有什么缓解办法？", CATEGORY_GENERAL),
        # ---- 边界：空 query 预期降级为技术咨询 ----
        ("", CATEGORY_TECH),
    ]

    total = len(test_cases)
    passed = 0
    for idx, (query, expected) in enumerate(test_cases, start=1):
        result = classify(query)
        ok = result.category == expected
        passed += 1 if ok else 0
        print(
            f"[{idx}/{total}] {'PASS' if ok else 'FAIL'} | 期望={expected} -> 实际={result.category} "
            f"(confidence={result.confidence:.4f})\n"
            f"    query={query!r}"
        )
    print(f"\n共 {total} 条测试，通过 {passed} 条，失败 {total - passed} 条")


if __name__ == "__main__":
    main()