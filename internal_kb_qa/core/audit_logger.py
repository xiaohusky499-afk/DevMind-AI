"""T14 通用日志与审计接口。

对外接口（与《企业内部技术知识库智能问答系统-任务分工》T14 契约一致）：
    log(level: str, event: str, fields: dict = None) -> None
    query_audit_logs(filters: dict = None) -> list[AuditLog]

实现要点：
1. 基于 Python logging 体系，输出结构化 JSON，一行一条，便于 ELK/Loki 采集；
2. 统一字段：timestamp、level、event、trace_id、user、team、query、
   hit_docs、security_level、latency，额外业务字段原样保留；
3. 敏感信息写入前脱敏（密钥、Token、密码、内网 IP、手机号、邮箱、身份证号等）；
4. 后台线程异步落盘，log() 调用不阻塞主链路；
5. query_audit_logs 支持按 user / team / event / time_range 查询，查询前会
   将待写队列刷盘，保证“先写后查”可见。
"""
from __future__ import annotations

import contextlib
import contextvars
import glob
import json
import logging
import logging.handlers
import os
import queue
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator, Mapping

# ---------------------------------------------------------------------------
# 常量与事件名
# ---------------------------------------------------------------------------

ALLOWED_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_LEVEL_ALIASES = {"WARN": "WARNING"}
_LEVELS_TO_LOGGING = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

STANDARD_FIELDS = (
    "timestamp",
    "level",
    "event",
    "trace_id",
    "user",
    "team",
    "query",
    "hit_docs",
    "security_level",
    "latency",
)

MASK = "***"


class AuditEvents:
    """常用审计事件名（可直接使用，也允许自定义 event）。"""

    QUERY_START = "query.start"
    FAQ_HIT = "faq.hit"
    RAG_DONE = "rag.done"
    AUDIT_ACCESS = "audit.access"
    HUMAN_HANDOFF = "human.handoff"
    FEEDBACK_SUBMIT = "feedback.submit"
    QUERY_ERROR = "query.error"


# ---------------------------------------------------------------------------
# trace_id 上下文
# ---------------------------------------------------------------------------

_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "devmind_ai_trace_id", default=None
)


def generate_trace_id() -> str:
    """生成新的 trace_id。"""
    return uuid.uuid4().hex


def get_trace_id() -> str | None:
    """读取当前上下文的 trace_id。"""
    return _trace_id_var.get()


def set_trace_id(trace_id: str | None = None) -> str:
    """设置当前上下文 trace_id，并返回生效值。"""
    value = trace_id or generate_trace_id()
    _trace_id_var.set(value)
    return value


@contextlib.contextmanager
def use_trace_id(trace_id: str | None = None) -> Iterator[str]:
    """在 with 块内使用指定（或新建）trace_id，退出后自动恢复。"""
    token = _trace_id_var.set(trace_id or generate_trace_id())
    try:
        yield _trace_id_var.get()
    finally:
        _trace_id_var.reset(token)


# ---------------------------------------------------------------------------
# 敏感信息脱敏
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"secret[_-]?key|private[_-]?key|authorization|cookie|credential|credentials)",
    re.IGNORECASE,
)

_KEY_VALUE_RE = re.compile(
    r"(?ix)"
    r"\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"secret[_-]?key|private[_-]?key|authorization|cookie|credential|credentials)"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&|]+)"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
_SK_KEY_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{6,}\b")
_AKIA_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _mask_phone(match: re.Match[str]) -> str:
    text = match.group(0)
    return f"{text[:3]}****{text[-4:]}"


def mask_text(text: str) -> str:
    """对字符串中的敏感信息做规则脱敏。

    覆盖：私钥、key=value 密钥、JWT、Bearer Token、sk-*、AKIA*、
    邮箱、手机号、身份证号、IP 地址。
    """
    if not text:
        return text

    text = _PRIVATE_KEY_RE.sub(MASK, text)
    # Bearer 需先于 key=value 处理，避免 "Authorization: Bearer abc" 中
    # key=value 规则先消费 "Bearer" 而把真实 token "abc" 漏掉。
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}{MASK}", text)
    text = _KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = _JWT_RE.sub(MASK, text)
    text = _SK_KEY_RE.sub(MASK, text)
    text = _AKIA_RE.sub(MASK, text)
    text = _EMAIL_RE.sub(MASK, text)
    text = _PHONE_RE.sub(_mask_phone, text)
    text = _ID_CARD_RE.sub(MASK, text)
    text = _IP_RE.sub(MASK, text)
    return text


def _is_sensitive_key(key: Any) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key)))


def desensitize(value: Any, _seen: set[int] | None = None) -> Any:
    """递归脱敏 dict/list/str/dataclass/Pydantic 等结构。"""
    if _seen is None:
        _seen = set()

    if isinstance(value, str):
        return mask_text(value)

    if isinstance(value, Mapping):
        value_id = id(value)
        if value_id in _seen:
            return "<recursive>"
        _seen.add(value_id)
        result = {
            str(key): (MASK if _is_sensitive_key(key) else desensitize(item, _seen))
            for key, item in value.items()
        }
        _seen.discard(value_id)
        return result

    if isinstance(value, (list, tuple, set, frozenset)):
        value_id = id(value)
        if value_id in _seen:
            return ["<recursive>"]
        _seen.add(value_id)
        result = [desensitize(item, _seen) for item in value]
        _seen.discard(value_id)
        return result

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    # dataclass
    if hasattr(value, "__dataclass_fields__"):
        return desensitize(
            {key: getattr(value, key) for key in value.__dataclass_fields__}, _seen
        )

    # Pydantic v2 / v1
    if hasattr(value, "model_dump"):
        try:
            return desensitize(value.model_dump(mode="json"), _seen)
        except TypeError:
            return desensitize(value.model_dump(), _seen)
    if hasattr(value, "dict") and callable(value.dict):
        return desensitize(value.dict(), _seen)

    # 其他对象降级为字符串，并过一遍脱敏
    return mask_text(str(value))


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_audit_record(
    level: str,
    event: str,
    fields: Mapping[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """按 T14 统一字段构造一条可 JSON 序列化的审计记录。"""
    if not isinstance(event, str) or not event.strip():
        raise ValueError("event 必须是非空字符串")
    if fields is None:
        fields = {}
    if not isinstance(fields, Mapping):
        raise TypeError("fields 必须是 dict/Mapping 或 None")

    raw_fields: dict[str, Any] = dict(fields)

    # 统一字段先从原始 fields 中取出；user/team/trace_id 是审计关联标识，
    # 不做内容脱敏，否则会破坏“按 user 查询审计日志”的可追溯性。
    provided_trace_id = raw_fields.pop("trace_id", None)
    user = _as_optional_str(raw_fields.pop("user", None))
    team = _as_optional_str(raw_fields.pop("team", None))
    security_level = _as_optional_str(raw_fields.pop("security_level", None))
    latency = _as_optional_float(raw_fields.pop("latency", None))

    # timestamp / level / event 由系统生成，禁止业务字段覆盖。
    for reserved_key in ("timestamp", "level", "event"):
        raw_fields.pop(reserved_key, None)

    # 剩余业务字段（含 query、hit_docs）递归脱敏。
    clean_fields: dict[str, Any] = desensitize(raw_fields)

    normalized_level = _normalize_level(level)
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    timestamp = (now[:-6] + "Z") if now.endswith("+00:00") else now
    effective_trace_id = (
        _as_optional_str(provided_trace_id or trace_id or get_trace_id())
        or generate_trace_id()
    )

    record: dict[str, Any] = {
        "timestamp": timestamp,
        "level": normalized_level,
        "event": event.strip(),
        "trace_id": effective_trace_id,
        "user": user,
        "team": team,
        "query": _as_optional_str(clean_fields.pop("query", None)),
        "hit_docs": clean_fields.pop("hit_docs", None),
        "security_level": security_level,
        "latency": latency,
    }

    # 除统一字段外的业务字段原样保留（已经过脱敏），便于后续扩展与检索。
    record.update(clean_fields)
    return record


def _normalize_level(level: str) -> str:
    if not isinstance(level, str):
        raise TypeError("level 必须是字符串")
    normalized = level.strip().upper()
    normalized = _LEVEL_ALIASES.get(normalized, normalized)
    if normalized not in ALLOWED_LEVELS:
        raise ValueError(f"level 仅支持 {ALLOWED_LEVELS}，收到: {level!r}")
    return normalized


def _json_default(value: Any) -> Any:
    """json.dumps 的兜底转换。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if hasattr(value, "__dataclass_fields__"):
        return {key: getattr(value, key) for key in value.__dataclass_fields__}
    return str(value)


# ---------------------------------------------------------------------------
# AuditLog 查询结果模型
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AuditLog:
    """一条审计日志记录。"""

    timestamp: str
    level: str
    event: str
    trace_id: str | None = None
    user: str | None = None
    team: str | None = None
    query: str | None = None
    hit_docs: Any = None
    security_level: str | None = None
    latency: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditLog":
        extra = {
            key: value for key, value in data.items() if key not in STANDARD_FIELDS
        }
        return cls(
            timestamp=data.get("timestamp", ""),
            level=data.get("level", ""),
            event=data.get("event", ""),
            trace_id=data.get("trace_id"),
            user=data.get("user"),
            team=data.get("team"),
            query=data.get("query"),
            hit_docs=data.get("hit_docs"),
            security_level=data.get("security_level"),
            latency=_as_optional_float(data.get("latency")),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为 dict，先统一字段，后附加业务字段。"""
        data: dict[str, Any] = {
            "timestamp": self.timestamp,
            "level": self.level,
            "event": self.event,
            "trace_id": self.trace_id,
            "user": self.user,
            "team": self.team,
            "query": self.query,
            "hit_docs": self.hit_docs,
            "security_level": self.security_level,
            "latency": self.latency,
        }
        data.update(self.extra)
        return data


# ---------------------------------------------------------------------------
# 异步写入器：后台线程 + 可等待 flush
# ---------------------------------------------------------------------------

class _AsyncJsonWriter:
    """使用后台线程消费队列并写入 RotatingFileHandler。

    log() 只负责入队，文件 I/O 在后台线程执行；flush() 通过队列哨兵
    保证调用点之前的日志全部落盘。
    """

    _WRITE = "write"
    _FLUSH = "flush"
    _CLOSE = "close"

    def __init__(
        self,
        handlers: list[logging.Handler],
        flush_timeout: float = 5.0,
    ) -> None:
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._handlers = handlers
        self._flush_timeout = flush_timeout
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="devmind-audit-log-writer",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, line: str) -> None:
        if self._closed:
            raise RuntimeError("audit logger 已关闭")
        self._queue.put((self._WRITE, line))

    def flush(self, timeout: float | None = None) -> bool:
        """等待队列中此前所有日志写入完成。"""
        if self._closed:
            return True
        done = threading.Event()
        self._queue.put((self._FLUSH, done))
        return done.wait(self._flush_timeout if timeout is None else timeout)

    def close(self, timeout: float | None = None) -> None:
        if self._closed:
            return
        done = threading.Event()
        self._queue.put((self._CLOSE, done))
        done.wait(self._flush_timeout if timeout is None else timeout)
        self._closed = True

    def _emit_line(self, line: str) -> None:
        record = logging.LogRecord(
            name="devmind-ai.audit",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=line,
            args=(),
            exc_info=None,
        )
        for handler in self._handlers:
            # handler.handle 会做异常兜底，避免磁盘/编码故障杀死后台写线程。
            handler.handle(record)

    def _run(self) -> None:
        while True:
            kind, payload = self._queue.get()
            try:
                if kind == self._WRITE:
                    self._emit_line(payload)
                elif kind == self._FLUSH:
                    for handler in self._handlers:
                        handler.flush()
                    payload.set()
                elif kind == self._CLOSE:
                    for handler in self._handlers:
                        handler.flush()
                        handler.close()
                    payload.set()
                    return
            finally:
                self._queue.task_done()


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------

class AuditLogger:
    """T14 通用日志接口实现。

    典型用法：
        logger = get_audit_logger()
        logger.log("INFO", "query.start", {"user": "zhangsan", "query": "..."})
        records = logger.query_audit_logs({"user": "zhangsan", "event": "query.start"})
    """

    def __init__(
        self,
        log_file: str | None = None,
        *,
        name: str = "devmind-ai.audit",
        level: str = "DEBUG",
        console: bool = False,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 10,
        flush_timeout: float = 5.0,
    ) -> None:
        self.name = name
        self.log_file = os.path.abspath(log_file or _default_log_file())
        normalized_level = _normalize_level(level)

        log_dir = os.path.dirname(self.log_file) or "."
        os.makedirs(log_dir, exist_ok=True)

        self._logger = logging.getLogger(name)
        self._logger.setLevel(_LEVELS_TO_LOGGING[normalized_level])
        self._logger.propagate = False

        formatter = logging.Formatter("%(message)s")

        file_handler = logging.handlers.RotatingFileHandler(
            self.log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        # 级别过滤统一在 AuditLogger.log() 内完成；这里放开 handler 级别，
        # 避免 LogRecord 默认 INFO 级别导致 ERROR 日志被 handler 误过滤。
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        handlers: list[logging.Handler] = [file_handler]
        if console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(formatter)
            handlers.append(console_handler)

        self._writer = _AsyncJsonWriter(handlers, flush_timeout=flush_timeout)
        self._closed = False

    # ------------------------------------------------------------------
    def log(
        self,
        level: str,
        event: str,
        fields: Mapping[str, Any] | None = None,
    ) -> None:
        """写入一条结构化审计日志。

        level: DEBUG / INFO / WARNING / ERROR（兼容小写与 WARN）。
        event: 事件名，例如 query.start / faq.hit / rag.done / audit.access。
        fields: 业务字段，自动附加 timestamp 与 trace_id，并先脱敏。
        """
        normalized_level = _normalize_level(level)
        if self._closed:
            raise RuntimeError("audit logger 已关闭")

        # 级别过滤，避免无效序列化开销
        if not self._logger.isEnabledFor(_LEVELS_TO_LOGGING[normalized_level]):
            return

        record = build_audit_record(normalized_level, event, fields)
        try:
            line = json.dumps(record, ensure_ascii=False, default=_json_default)
        except TypeError:
            # 极端兜底：确保任何业务对象都不会阻塞主链路
            line = json.dumps(
                desensitize(record), ensure_ascii=False, default=str
            )
        self._writer.enqueue(line)

    # ------------------------------------------------------------------
    def query_audit_logs(
        self,
        filters: Mapping[str, Any] | None = None,
    ) -> list[AuditLog]:
        """按条件查询审计日志。

        filters 支持：
            user:       精确匹配用户
            team:       精确匹配团队
            event:      精确匹配事件名
            level:      精确匹配级别（扩展字段）
            time_range: (start, end) / {"start": ..., "end": ...} / "start,end"
            start_time / end_time: 单独指定时间边界
        查询前会 flush 待写队列，保证刚写入的日志可查。
        """
        filter_dict = dict(filters or {})
        start_dt, end_dt = _parse_time_range(filter_dict)

        # 先把队列刷盘，保证“先写后查”可见
        self._writer.flush()

        records: list[AuditLog] = []
        for path in _iter_log_files(self.log_file):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(item, dict):
                            continue
                        if not _matches_filters(item, filter_dict, start_dt, end_dt):
                            continue
                        records.append(AuditLog.from_dict(item))
            except OSError:
                continue

        records.sort(key=lambda record: record.timestamp)
        return records

    # ------------------------------------------------------------------
    def flush(self, timeout: float | None = None) -> bool:
        """将待写日志刷盘，返回是否在超时时间内完成。"""
        return self._writer.flush(timeout)

    def close(self, timeout: float | None = None) -> None:
        """关闭后台写入线程与文件句柄。"""
        if self._closed:
            return
        self._writer.close(timeout)
        self._closed = True

    def __enter__(self) -> "AuditLogger":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# 时间范围与过滤
# ---------------------------------------------------------------------------

def _parse_datetime(value: Any, *, is_end: bool = False) -> datetime:
    """将时间过滤值解析为带时区的 datetime。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        text = str(value).strip()
        # 仅日期：start 为当天 00:00，end 为当天 23:59:59.999999
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            dt = datetime.fromisoformat(text)
            if is_end:
                dt = dt + timedelta(days=1, microseconds=-1)
        else:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_time_range(
    filters: Mapping[str, Any],
) -> tuple[datetime | None, datetime | None]:
    """解析 time_range / start_time / end_time，返回 UTC 边界。"""
    raw_range = filters.get("time_range")
    start_value = filters.get("start_time")
    end_value = filters.get("end_time")

    if raw_range is not None:
        if isinstance(raw_range, Mapping):
            start_value = raw_range.get("start", start_value)
            end_value = raw_range.get("end", end_value)
        elif isinstance(raw_range, (tuple, list)) and len(raw_range) >= 2:
            start_value, end_value = raw_range[0], raw_range[1]
        elif isinstance(raw_range, str):
            if "," in raw_range:
                parts = [part.strip() for part in raw_range.split(",", 1)]
                start_value, end_value = parts[0], parts[1]
            elif "~" in raw_range:
                parts = [part.strip() for part in raw_range.split("~", 1)]
                start_value, end_value = parts[0], parts[1]
            else:
                raise ValueError("time_range 字符串需为 'start,end' 或 'start~end'")
        else:
            raise TypeError("time_range 仅支持 tuple/list/dict/str")

    start_dt = _parse_datetime(start_value) if start_value is not None else None
    end_dt = _parse_datetime(end_value, is_end=True) if end_value is not None else None

    if start_dt and end_dt and start_dt > end_dt:
        raise ValueError("time_range 的 start 不能晚于 end")
    return start_dt, end_dt


def _record_datetime(record: Mapping[str, Any]) -> datetime | None:
    timestamp = record.get("timestamp")
    if not timestamp:
        return None
    try:
        return _parse_datetime(timestamp)
    except (TypeError, ValueError):
        return None


def _matches_filters(
    record: Mapping[str, Any],
    filters: Mapping[str, Any],
    start_dt: datetime | None,
    end_dt: datetime | None,
) -> bool:
    for key in ("user", "team", "event", "level"):
        expected = filters.get(key)
        if expected is None:
            continue
        if str(record.get(key)) != str(expected):
            return False

    record_dt = _record_datetime(record)
    if start_dt and (record_dt is None or record_dt < start_dt):
        return False
    if end_dt and (record_dt is None or record_dt > end_dt):
        return False
    return True


def _iter_log_files(log_file: str):
    """返回主日志文件与滚动备份文件。"""
    pattern = f"{glob.escape(log_file)}*"
    for path in sorted(glob.glob(pattern)):
        if os.path.isfile(path):
            yield path


# ---------------------------------------------------------------------------
# 默认实例与模块级接口
# ---------------------------------------------------------------------------

def _default_log_file() -> str:
    env_path = os.getenv("AUDIT_LOG_FILE")
    if env_path:
        return env_path

    # 默认写入与应用日志同目录的 audit.log，例如 logs/audit.log
    log_dir = os.getenv("AUDIT_LOG_DIR", "logs")
    try:
        from base.config import Config  # 延迟导入，避免与配置模块循环依赖

        configured = Config().LOG_FILE
        if configured:
            log_dir = os.path.dirname(configured) or log_dir
    except Exception:
        # 配置文件缺失/加载失败时仍保证日志接口可用
        pass
    return os.path.join(log_dir, "audit.log")


_loggers: dict[tuple[str, str], AuditLogger] = {}
_loggers_lock = threading.Lock()
_default_logger: AuditLogger | None = None


def get_audit_logger(
    log_file: str | None = None,
    *,
    name: str = "devmind-ai.audit",
    level: str | None = None,
    console: bool | None = None,
) -> AuditLogger:
    """获取（或创建）AuditLogger 实例。

    同一 (name, log_file) 返回同一实例，避免重复创建文件句柄与后台线程。
    可用环境变量覆盖默认值：
        AUDIT_LOG_FILE / AUDIT_LOG_DIR / AUDIT_LOG_LEVEL / AUDIT_LOG_CONSOLE
        AUDIT_LOG_MAX_BYTES / AUDIT_LOG_BACKUP_COUNT
    """
    resolved_file = os.path.abspath(log_file or _default_log_file())
    key = (name, resolved_file)
    with _loggers_lock:
        if key in _loggers:
            return _loggers[key]

        instance = AuditLogger(
            resolved_file,
            name=name,
            level=level or os.getenv("AUDIT_LOG_LEVEL", "DEBUG"),
            console=(
                _env_flag("AUDIT_LOG_CONSOLE") if console is None else console
            ),
            max_bytes=int(os.getenv("AUDIT_LOG_MAX_BYTES", str(10 * 1024 * 1024))),
            backup_count=int(os.getenv("AUDIT_LOG_BACKUP_COUNT", "10")),
        )
        _loggers[key] = instance
        return instance


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _get_default() -> AuditLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = get_audit_logger()
    return _default_logger


def log(
    level: str,
    event: str,
    fields: dict | None = None,
) -> None:
    """T14 对外接口：写入一条结构化审计日志。"""
    _get_default().log(level, event, fields)


def query_audit_logs(
    filters: dict | None = None,
) -> list[AuditLog]:
    """T14 对外接口：按 user / team / event / time_range 查询审计日志。"""
    return _get_default().query_audit_logs(filters)


def close_all() -> None:
    """关闭所有已注册 AuditLogger 的后台线程与文件句柄。"""
    global _default_logger
    with _loggers_lock:
        loggers = list(_loggers.values())
        _loggers.clear()
        _default_logger = None
    for instance in loggers:
        instance.close()


# 进程退出时兜底 flush/close
import atexit

atexit.register(close_all)


