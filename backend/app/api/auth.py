"""认证接口：验证码、注册、登录、退出。"""
from fastapi import APIRouter, Depends, Request

from ..core.config import settings
from ..core.db import db
from ..core.rate_limit import client_ip, enforce
from ..core.responses import ok
from ..core.security import blacklist_token, create_access_token, get_current_user
from ..schemas import (
    LoginRequest,
    RegisterRequest,
    SendCodeRequest,
    SmsLoginRequest,
    TokenResponse,
)
from ..services import user_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/send-code")
async def send_code(req: SendCodeRequest, request: Request):
    """发送手机验证码。开发环境返回 debug_code 方便联调。"""
    ip = client_ip(request)
    await enforce(db.redis_sms, "sms:ip:hour", ip, settings.SMS_SEND_IP_HOURLY_LIMIT, 3600)
    await enforce(
        db.redis_sms,
        "sms:phone:interval",
        req.phone,
        1,
        settings.SMS_SEND_INTERVAL,
    )
    await enforce(
        db.redis_sms,
        "sms:phone:day",
        req.phone,
        settings.SMS_SEND_DAILY_LIMIT,
        86400,
    )
    return ok(request, await user_service.send_sms_code(req.phone))


@router.post("/register")
async def register(req: RegisterRequest, request: Request):
    """注册账号：用户名 + 密码 + 手机验证码。"""
    ip = client_ip(request)
    await enforce(
        db.redis_sms, "register:ip", ip, settings.REGISTER_IP_HOURLY_LIMIT, 3600
    )
    await enforce(
        db.redis_sms, "sms:verify", f"{ip}:{req.phone}", settings.SMS_VERIFY_LIMIT, 600
    )
    user = await user_service.register_user(
        username=req.username,
        password=req.password,
        phone=req.phone,
        sms_code=req.sms_code,
        nickname=req.nickname,
        team=req.team,
    )
    return ok(request, TokenResponse(access_token=create_access_token(user), user=user))


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    """账号密码登录，account 支持用户名或手机号。"""
    ip = client_ip(request)
    await enforce(
        db.redis_token,
        "login:ip",
        ip,
        settings.LOGIN_IP_LIMIT,
        settings.LOGIN_RATE_WINDOW_SECONDS,
    )
    await enforce(
        db.redis_token,
        "login:account",
        req.account,
        settings.LOGIN_ACCOUNT_LIMIT,
        settings.LOGIN_RATE_WINDOW_SECONDS,
    )
    user = await user_service.login_by_account(req.account, req.password)
    return ok(request, TokenResponse(access_token=create_access_token(user), user=user))


@router.post("/login/sms")
async def login_by_sms(req: SmsLoginRequest, request: Request):
    """手机号 + 验证码登录。"""
    ip = client_ip(request)
    await enforce(
        db.redis_token,
        "login:sms:ip",
        ip,
        settings.LOGIN_IP_LIMIT,
        settings.LOGIN_RATE_WINDOW_SECONDS,
    )
    await enforce(
        db.redis_sms, "sms:verify", f"{ip}:{req.phone}", settings.SMS_VERIFY_LIMIT, 600
    )
    user = await user_service.login_by_sms(req.phone, req.sms_code)
    return ok(request, TokenResponse(access_token=create_access_token(user), user=user))


@router.post("/logout")
async def logout(request: Request, current_user: dict = Depends(get_current_user)):
    """退出登录：将当前 JWT 的 jti 加入 Redis 黑名单。"""
    await blacklist_token(current_user)
    return ok(request, {"message": "退出成功"})
