"""Live PostgreSQL check for temporary account lock and recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException

from backend.app.core.config import settings
from backend.app.core.db import db
from backend.app.core.rate_limit import enforce, limit_key
from backend.app.core.security import hash_password
from backend.app.services.user_service import login_by_account


async def main() -> None:
    user_id = uuid4()
    username = f"security-{user_id.hex[:12]}"
    password = "SafePassword123"
    await db.connect()
    try:
        rate_identifier = str(user_id)
        await enforce(db.redis_token, "live-security", rate_identifier, 1, 60)
        try:
            await enforce(db.redis_token, "live-security", rate_identifier, 1, 60)
        except HTTPException as exc:
            assert exc.status_code == 429
            assert exc.headers["Retry-After"]
        else:
            raise AssertionError("Redis rate limit was not enforced")

        await db.execute(
            """INSERT INTO users
               (id, organization_id, username, password_hash, nickname, team)
               VALUES ($1,'00000000-0000-0000-0000-000000000001',$2,$3,$2,'security-test')""",
            (user_id, username, hash_password(password)),
        )
        for _ in range(settings.LOGIN_LOCK_THRESHOLD):
            try:
                await login_by_account(username, "wrong-password")
            except HTTPException as exc:
                assert exc.status_code == 400
            else:
                raise AssertionError("wrong password was accepted")

        locked = await db.fetch_one(
            "SELECT status, failed_login_count, locked_until FROM users WHERE id=$1",
            (user_id,),
        )
        assert locked["status"] == "locked"
        assert locked["failed_login_count"] == settings.LOGIN_LOCK_THRESHOLD
        assert locked["locked_until"] > datetime.now(UTC)

        try:
            await login_by_account(username, password)
        except HTTPException as exc:
            assert exc.status_code == 423
        else:
            raise AssertionError("temporarily locked account was accepted")

        await db.execute(
            "UPDATE users SET locked_until=$2 WHERE id=$1",
            (user_id, datetime.now(UTC) - timedelta(seconds=1)),
        )
        user = await login_by_account(username, password)
        assert user["id"] == user_id
        recovered = await db.fetch_one(
            "SELECT status, failed_login_count, locked_until FROM users WHERE id=$1",
            (user_id,),
        )
        assert recovered == {
            "status": "active",
            "failed_login_count": 0,
            "locked_until": None,
        }
        print("auth security live check passed")
    finally:
        await db.redis_token.delete(limit_key("live-security", str(user_id)))
        await db.execute("DELETE FROM users WHERE id=$1", (user_id,))
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
