"""API 请求/响应模型。"""

from datetime import UTC, date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

# ---------------- 用户认证 ----------------


class SendCodeRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20, description="手机号")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64, description="登录账号")
    password: str = Field(min_length=6, max_length=64, description="密码")
    nickname: str | None = Field(default=None, max_length=100)
    phone: str = Field(min_length=6, max_length=20)
    sms_code: str = Field(min_length=4, max_length=8)
    team: str = "default"


class LoginRequest(BaseModel):
    account: str = Field(min_length=2, max_length=64, description="用户名或手机号")
    password: str = Field(min_length=1, max_length=64)


class SmsLoginRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    sms_code: str = Field(min_length=4, max_length=8)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


# ---------------- 用户资料 ----------------


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    avatar_url: str | None = Field(default=None, max_length=2048)
    gender: Literal["male", "female", "unspecified"] | None = None
    birth_date: date | None = None
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    phone_verification_code: str | None = Field(
        default=None, min_length=4, max_length=8
    )
    department_name: str | None = Field(default=None, max_length=128)
    job_title: str | None = Field(default=None, max_length=128)
    employee_no: str | None = Field(default=None, max_length=64)
    joined_at: date | None = None
    bio: str | None = Field(default=None, max_length=500)
    timezone: str | None = Field(default=None, max_length=64)
    locale: Literal["zh-CN", "en-US"] | None = None

    @field_validator(
        "display_name", "department_name", "job_title", "employee_no", "email"
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value else None

    @field_validator("birth_date", "joined_at")
    @classmethod
    def date_cannot_be_future(cls, value: date | None) -> date | None:
        if value and value > datetime.now(UTC).date():
            raise ValueError("日期不得晚于今天")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value and ("@" not in value or value.startswith("@") or value.endswith("@")):
            raise ValueError("邮箱格式不正确")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("时区格式不正确") from exc
        return value


# ---------------- 知识库 ----------------


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class KnowledgeBaseUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["ready", "disabled"] | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class KnowledgeBaseMemberRequest(BaseModel):
    role: Literal["viewer", "editor", "admin"]


class DocumentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    metadata: dict | None = None

    @field_validator("name")
    @classmethod
    def strip_document_name(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class DocumentReindexRequest(BaseModel):
    force: bool = False


# ---------------- 智能问答 ----------------


class SessionCreateRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=50)


class SessionUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return value.strip()


class PreferencesUpdateRequest(BaseModel):
    default_knowledge_base_ids: list[str] | None = Field(default=None, max_length=50)
    default_mode: Literal["tech", "troubleshoot", "summarize"] | None = None
    locale: Literal["zh-CN", "en-US"] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    answer_style: Literal["concise", "balanced", "detailed"] | None = None

    @field_validator("timezone")
    @classmethod
    def validate_preference_timezone(cls, value: str | None) -> str | None:
        if value:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("时区格式不正确") from exc
        return value


class MessageCreateRequest(BaseModel):
    session_id: str
    content: str = Field(min_length=1, max_length=20_000)
    mode: Literal["tech", "troubleshoot", "summarize"] = "tech"
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=50)
    parent_message_id: str | None = None

    @field_validator("content")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class RetrievalTestRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=50)
    top_k: int = Field(default=20, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=20)


class DocumentQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=100)
    keyword: str | None = None
    doc_type: str | None = None
    team: str | None = None


class FAQQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=100)
    keyword: str | None = None
    category: str | None = None
    team: str | None = None


class FAQSearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
