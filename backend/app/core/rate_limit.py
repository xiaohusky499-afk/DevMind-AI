"""Atomic Redis fixed-window limits without storing raw identifiers."""

import hashlib

from fastapi import HTTPException, Request

_INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def limit_key(scope: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.strip().casefold().encode("utf-8")).hexdigest()
    return f"rate:{scope}:{digest}"


async def enforce(
    redis_client,
    scope: str,
    identifier: str,
    limit: int,
    window_seconds: int,
) -> None:
    count, ttl = await redis_client.eval(
        _INCREMENT_SCRIPT, 1, limit_key(scope, identifier), window_seconds
    )
    if int(count) > limit:
        retry_after = max(1, int(ttl))
        raise HTTPException(
            status_code=429,
            detail={
                "code": "RATE_LIMITED",
                "message": "请求过于频繁，请稍后再试",
                "details": {"retry_after": retry_after},
            },
            headers={"Retry-After": str(retry_after)},
        )
