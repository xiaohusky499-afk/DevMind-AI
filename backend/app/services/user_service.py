"""用户服务：短信验证码、注册、登录、用户信息。"""

import asyncio
import json
import secrets
from datetime import UTC, datetime

from asyncpg import UniqueViolationError
from fastapi import HTTPException

from ..core.config import settings
from ..core.db import db
from ..core.security import hash_password, verify_password

_SMS_CODE_PREFIX = "sms:code:"
_DUMMY_PASSWORD_HASH = hash_password("invalid-login-password")
async def send_sms_code(phone: str) -> dict:
    """发送手机验证码（开发环境直接返回 debug_code）。"""
    # 接口层已经执行手机号、IP 和每日发送限流。
    code = str(secrets.randbelow(900000) + 100000)
    await db.redis_sms.set(f"{_SMS_CODE_PREFIX}{phone}", code, ex=settings.SMS_CODE_TTL)

    # 实际项目在这里调用短信服务商 API
    # TODO: 接入阿里云/腾讯云短信，生产环境不要返回 debug_code
    result = {"message": "验证码已发送", "expire_seconds": settings.SMS_CODE_TTL}
    if settings.APP_DEBUG:
        result["debug_code"] = code
    return result


async def verify_sms_code(phone: str, code: str) -> None:
    """校验短信验证码，校验成功后删除，防止重复使用。"""
    key = f"{_SMS_CODE_PREFIX}{phone}"
    saved = await db.redis_sms.get(key)
    if not saved or saved != code:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    await db.redis_sms.delete(key)


async def register_user(
    username: str,
    password: str,
    phone: str,
    sms_code: str,
    nickname: str | None = None,
    team: str = "default",
) -> dict:
    """用户注册。"""
    await verify_sms_code(phone, sms_code)

    # 用户名和手机号唯一性检查
    if await db.fetch_one("SELECT id FROM users WHERE username=$1", (username,)):
        raise HTTPException(status_code=400, detail="用户名已存在")
    if await db.fetch_one("SELECT id FROM users WHERE phone=$1", (phone,)):
        raise HTTPException(status_code=400, detail="手机号已注册")

    try:
        created = await db.fetch_one(
            """INSERT INTO users (username, password_hash, nickname, phone, team, security_level)
               VALUES ($1, $2, $3, $4, $5, 'team')
               RETURNING id""",
            (
                username,
                await asyncio.to_thread(hash_password, password),
                nickname or username,
                phone,
                team,
            ),
        )
        user_id = created["id"]
    except UniqueViolationError:
        # 并发注册时，两个请求可能同时通过前面的唯一性检查，这里兜底
        raise HTTPException(status_code=400, detail="用户名或手机号已存在")
    return {
        "id": user_id,
        "username": username,
        "nickname": nickname or username,
        "phone": phone,
        "team": team,
        "security_level": "team",
    }


async def login_by_account(account: str, password: str) -> dict:
    """账号密码登录，account 支持用户名或手机号。"""
    result = None
    failure = None
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE username=$1 OR phone=$2 LIMIT 1 FOR UPDATE",
            account,
            account,
        )
        user = dict(row) if row else None
        now = datetime.now(UTC)
        if user and (not user["is_active"] or user["status"] == "disabled"):
            failure = HTTPException(status_code=403, detail="账号已被禁用")
        elif user and user["status"] == "locked" and (
            not user.get("locked_until") or user["locked_until"] > now
        ):
            retry_after = (
                max(1, int((user["locked_until"] - now).total_seconds()))
                if user.get("locked_until")
                else None
            )
            failure = HTTPException(
                status_code=423,
                detail={
                    "code": "ACCOUNT_TEMPORARILY_LOCKED",
                    "message": "登录失败次数过多，账号已临时锁定",
                    "details": {"retry_after": retry_after} if retry_after else None,
                },
                headers={"Retry-After": str(retry_after)} if retry_after else None,
            )
        else:
            if user and user["status"] == "locked":
                await conn.execute(
                    """UPDATE users SET status='active', failed_login_count=0,
                              locked_until=NULL WHERE id=$1""",
                    user["id"],
                )
                user["status"] = "active"
                user["failed_login_count"] = 0
            password_valid = await asyncio.to_thread(
                verify_password,
                password,
                user["password_hash"] if user else _DUMMY_PASSWORD_HASH,
            )
            valid = bool(user and password_valid)
            if not valid:
                if user:
                    failed_count = int(user.get("failed_login_count") or 0) + 1
                    should_lock = failed_count >= settings.LOGIN_LOCK_THRESHOLD
                    await conn.execute(
                        """UPDATE users
                           SET failed_login_count=$2, last_failed_login_at=now(),
                               status=CASE WHEN $3 THEN 'locked' ELSE status END,
                               locked_until=CASE WHEN $3
                                   THEN now()+($4*interval '1 second') ELSE locked_until END
                           WHERE id=$1""",
                        user["id"],
                        failed_count,
                        should_lock,
                        settings.LOGIN_LOCK_SECONDS,
                    )
                failure = HTTPException(status_code=400, detail="用户名或密码错误")
            else:
                await conn.execute(
                    """UPDATE users SET failed_login_count=0, locked_until=NULL,
                              last_failed_login_at=NULL, last_login_at=now(), status='active'
                       WHERE id=$1""",
                    user["id"],
                )
                result = _public_user(user)
    if failure:
        raise failure
    return result


async def login_by_sms(phone: str, sms_code: str) -> dict:
    """手机号验证码登录。"""
    await verify_sms_code(phone, sms_code)
    user = await db.fetch_one("SELECT * FROM users WHERE phone=$1 LIMIT 1", (phone,))
    if not user:
        raise HTTPException(status_code=400, detail="该手机号未注册")
    if not user["is_active"] or user["status"] == "disabled":
        raise HTTPException(status_code=403, detail="账号已被禁用")

    await db.execute(
        """UPDATE users SET failed_login_count=0, locked_until=NULL,
                  last_failed_login_at=NULL, last_login_at=now(), status='active'
           WHERE id=$1""",
        (user["id"],),
    )

    return _public_user(user)


def _public_user(user: dict) -> dict:
    """只返回可暴露给前端的用户信息。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "nickname": user.get("nickname") or user["username"],
        "phone": user.get("phone"),
        "team": user.get("team"),
        "security_level": user.get("security_level"),
    }


_PROFILE_SELECT = """
SELECT
    u.id, u.organization_id, o.name AS organization_name,
    u.username, u.nickname, u.email, u.phone,
    COALESCE(p.display_name, u.nickname, u.username) AS display_name,
    p.avatar_url, COALESCE(p.gender, 'unspecified') AS gender,
    p.birth_date, p.department_name, p.job_title, p.employee_no, p.joined_at, p.bio,
    COALESCE(pref.timezone, 'Asia/Shanghai') AS timezone,
    COALESCE(pref.locale, 'zh-CN') AS locale,
    COALESCE(p.updated_at, u.updated_at) AS updated_at
FROM users u
JOIN organizations o ON o.id=u.organization_id
LEFT JOIN user_profiles p ON p.user_id=u.id AND p.organization_id=u.organization_id
LEFT JOIN user_preferences pref ON pref.user_id=u.id
WHERE u.id=$1 AND u.organization_id=$2
"""


async def get_profile(current_user: dict) -> dict:
    profile = await db.fetch_one(
        _PROFILE_SELECT,
        (current_user["id"], current_user["organization_id"]),
    )
    if not profile:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROFILE_NOT_FOUND", "message": "用户资料不存在"},
        )
    return _profile_payload(profile)


async def list_organization_users(
    current_user: dict, keyword: str | None, limit: int
) -> dict:
    params: list = [current_user["organization_id"]]
    condition = ""
    if keyword:
        params.append(f"%{keyword.strip()}%")
        condition = f"AND (u.username ILIKE $2 OR u.nickname ILIKE $2 OR p.display_name ILIKE $2 OR p.department_name ILIKE $2)"
    params.append(limit)
    rows = await db.fetch_all(
        f"""SELECT u.id AS user_id,u.username,
                   COALESCE(p.display_name,u.nickname,u.username) AS display_name,
                   p.department_name,p.job_title
            FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id
            WHERE u.organization_id=$1 AND u.is_active=true AND u.status='active'
              {condition}
            ORDER BY display_name LIMIT ${len(params)}""",
        tuple(params),
    )
    return {"items": rows}


def _profile_payload(profile: dict) -> dict:
    return {
        "display_name": profile["display_name"],
        "avatar_url": profile["avatar_url"],
        "gender": profile["gender"],
        "birth_date": profile["birth_date"],
        "email": profile["email"],
        "phone": profile["phone"],
        "department_name": profile["department_name"],
        "job_title": profile["job_title"],
        "employee_no": profile["employee_no"],
        "joined_at": profile["joined_at"],
        "bio": profile["bio"],
        "timezone": profile["timezone"],
        "locale": profile["locale"],
        "updated_at": profile["updated_at"],
        "organization": {
            "id": profile["organization_id"],
            "name": profile["organization_name"],
        },
        "editable_fields": [
            "display_name",
            "avatar_url",
            "gender",
            "birth_date",
            "phone",
            "department_name",
            "job_title",
            "employee_no",
            "joined_at",
            "bio",
            "timezone",
            "locale",
        ],
    }


async def update_profile(current_user: dict, values: dict, request_id: str) -> dict:
    """在同一事务中保存账号字段、个人档案、偏好和审计记录。"""
    phone_code = values.pop("phone_verification_code", None)
    user_id = current_user["id"]
    organization_id = current_user["organization_id"]

    async with db.postgres_pool.acquire() as conn, conn.transaction():
        account = await conn.fetchrow(
            "SELECT email, phone FROM users WHERE id=$1 AND organization_id=$2 FOR UPDATE",
            user_id,
            organization_id,
        )
        if not account:
            raise HTTPException(
                status_code=404,
                detail={"code": "PROFILE_NOT_FOUND", "message": "用户资料不存在"},
            )

        if "email" in values and values["email"] != account["email"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "EMAIL_VERIFICATION_REQUIRED",
                    "message": "修改企业邮箱前需要完成邮箱验证",
                },
            )
        if "phone" in values and values["phone"] != account["phone"]:
            if await conn.fetchval(
                "SELECT 1 FROM users WHERE phone=$1 AND id<>$2",
                values["phone"],
                user_id,
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PHONE_ALREADY_EXISTS",
                        "message": "该手机号已被使用",
                    },
                )
            if not phone_code:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PHONE_VERIFICATION_REQUIRED",
                        "message": "修改手机号前需要短信验证码",
                    },
                )
            await verify_sms_code(values["phone"], phone_code)

        profile_fields = {
            key: values.get(key)
            for key in (
                "display_name",
                "avatar_url",
                "gender",
                "birth_date",
                "department_name",
                "job_title",
                "employee_no",
                "joined_at",
                "bio",
            )
            if key in values
        }
        preference_fields = {
            key: values[key] for key in ("timezone", "locale") if key in values
        }

        employee_no = profile_fields.get("employee_no")
        if employee_no and await conn.fetchval(
            """SELECT 1 FROM user_profiles
                   WHERE organization_id=$1 AND employee_no=$2 AND user_id<>$3""",
            organization_id,
            employee_no,
            user_id,
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "EMPLOYEE_NO_ALREADY_EXISTS",
                    "message": "该工号已被使用",
                },
            )

        display_name = (
            profile_fields.get("display_name")
            or current_user.get("nickname")
            or current_user["username"]
        )
        await conn.execute(
            """INSERT INTO user_profiles (user_id, organization_id, display_name)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (user_id) DO NOTHING""",
            user_id,
            organization_id,
            display_name,
        )
        if profile_fields:
            assignments = ", ".join(
                f"{name}=${index}" for index, name in enumerate(profile_fields, start=3)
            )
            try:
                await conn.execute(
                    f"UPDATE user_profiles SET {assignments}, updated_at=now() WHERE user_id=$1 AND organization_id=$2",
                    user_id,
                    organization_id,
                    *profile_fields.values(),
                )
            except UniqueViolationError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "EMPLOYEE_NO_ALREADY_EXISTS",
                        "message": "该工号已被使用",
                    },
                ) from exc

        if "phone" in values and values["phone"] != account["phone"]:
            try:
                await conn.execute(
                    "UPDATE users SET phone=$2, phone_verified_at=now() WHERE id=$1",
                    user_id,
                    values["phone"],
                )
            except UniqueViolationError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PHONE_ALREADY_EXISTS",
                        "message": "该手机号已被使用",
                    },
                ) from exc
        if "display_name" in profile_fields:
            await conn.execute(
                "UPDATE users SET nickname=$2 WHERE id=$1",
                user_id,
                profile_fields["display_name"],
            )

        await conn.execute(
            "INSERT INTO user_preferences (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )
        if preference_fields:
            assignments = ", ".join(
                f"{name}=${index}"
                for index, name in enumerate(preference_fields, start=2)
            )
            await conn.execute(
                f"UPDATE user_preferences SET {assignments}, updated_at=now() WHERE user_id=$1",
                user_id,
                *preference_fields.values(),
            )

        changed_fields = sorted(
            key for key in values if key not in {"birth_date", "phone", "bio"}
        )
        await conn.execute(
            """INSERT INTO audit_logs
                   (organization_id, user_id, action, resource_type, resource_id, request_id, metadata)
                   VALUES ($1, $2, 'user.profile.updated', 'user_profile', $3, $4, $5::jsonb)""",
            organization_id,
            user_id,
            str(user_id),
            request_id,
            json.dumps({"changed_fields": changed_fields}),
        )

    return await get_profile(current_user)
