"""安全模块：密码哈希、JWT 签发/校验、当前登录用户依赖。"""

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .db import db
from .observability import bind_log_context
from .rate_limit import enforce

bearer_scheme = HTTPBearer(auto_error=False)

# 密码格式：pbkdf2_sha256$迭代次数$盐$哈希
_PBKDF2_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """生成密码哈希。"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码。"""
    try:
        algorithm, iterations, salt_hex, hash_hex = password_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (TypeError, ValueError):
        return False


def create_access_token(user: dict) -> str:
    """签发 JWT。"""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "nickname": user.get("nickname"),
        "phone": user.get("phone"),
        "team": user.get("team"),
        "security_level": user.get("security_level", "team"),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(hours=settings.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解析 JWT，失败统一抛 401。"""
    try:
        return jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或过期的登录凭证"
        )


async def is_token_blacklisted(jti: str) -> bool:
    """判断 JWT 是否已退出/拉黑。"""
    return bool(await db.redis_token.exists(f"jwt:blacklist:{jti}"))


async def blacklist_token(payload: dict) -> None:
    """将 JWT jti 加入黑名单，保留到过期时间。"""
    exp = payload.get("exp")
    if not exp:
        return
    ttl = int(exp) - int(datetime.now(UTC).timestamp())
    if ttl > 0:
        await db.redis_token.set(f"jwt:blacklist:{payload['jti']}", "1", ex=int(ttl))


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    """FastAPI 依赖：从 Authorization 中解析当前登录用户。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    return await get_user_from_token(credentials.credentials)


async def get_user_from_token(token: str) -> dict:
    """HTTP 与 WebSocket 共用的 JWT 用户解析。"""
    payload = decode_token(token)
    if await is_token_blacklisted(payload.get("jti", "")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的用户身份"
        ) from exc

    await enforce(
        db.redis_token,
        "jwt:user",
        str(user_id),
        settings.JWT_REQUEST_LIMIT,
        settings.JWT_RATE_WINDOW_SECONDS,
    )

    user = await db.fetch_one(
        """SELECT id, organization_id, username, nickname, email, phone, team, security_level
           FROM users WHERE id=$1 AND is_active=true AND status='active'""",
        (user_id,),
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已被禁用"
        )

    bind_log_context(user_id=user["id"], organization_id=user["organization_id"])

    return {
        **user,
        "jti": payload.get("jti"),
        "exp": payload.get("exp"),
    }
