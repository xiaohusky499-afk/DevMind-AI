"""运行中 PostgreSQL 的组织、知识库、文档权限边界验收。"""

import asyncio
from uuid import uuid4

import asyncpg
from fastapi import HTTPException

from backend.app.core.config import settings
from backend.app.core.db import db
from backend.app.services import (
    document_service,
    knowledge_base_service,
    knowledge_service,
    rag_service,
)


def assert_error(exc: HTTPException, status: int, code: str) -> None:
    assert exc.status_code == status
    assert exc.detail["code"] == code


async def expect_denied(call, status: int, code: str) -> None:
    try:
        await call
    except HTTPException as exc:
        assert_error(exc, status, code)
    else:
        raise AssertionError(f"expected {status} {code}")


async def main() -> None:
    connection = await asyncpg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database=settings.POSTGRES_DB,
    )
    transaction = connection.transaction()
    await transaction.start()
    original_pool = db.postgres_pool
    db.postgres_pool = _SingleConnectionPool(connection)
    try:
        org_a, org_b = uuid4(), uuid4()
        users = {name: uuid4() for name in ("viewer", "editor", "admin", "outsider")}
        kb_a, kb_b = uuid4(), uuid4()
        public_doc, team_doc, confidential_doc = uuid4(), uuid4(), uuid4()
        await connection.executemany(
            "INSERT INTO organizations(id,name,slug) VALUES($1,$2,$3)",
            [(org_a, "权限测试 A", f"perm-a-{org_a}"), (org_b, "权限测试 B", f"perm-b-{org_b}")],
        )
        for name, user_id in users.items():
            organization_id = org_b if name == "outsider" else org_a
            level = "confidential" if name == "admin" else "team"
            await connection.execute(
                """INSERT INTO users
                   (id,organization_id,username,password_hash,team,security_level)
                   VALUES($1,$2,$3,'test',$4,$5)""",
                user_id,
                organization_id,
                f"perm-{name}-{user_id}",
                "platform",
                level,
            )
        await connection.executemany(
            """INSERT INTO knowledge_bases(id,organization_id,name,status,created_by)
               VALUES($1,$2,$3,'ready',$4)""",
            [(kb_a, org_a, "权限知识库 A", users["admin"]), (kb_b, org_b, "权限知识库 B", users["outsider"])],
        )
        await connection.executemany(
            "INSERT INTO knowledge_base_members(knowledge_base_id,user_id,role) VALUES($1,$2,$3)",
            [
                (kb_a, users["viewer"], "viewer"),
                (kb_a, users["editor"], "editor"),
                (kb_a, users["admin"], "admin"),
                (kb_b, users["outsider"], "admin"),
                # 模拟绕过应用写入的脏数据；组织边界仍必须拒绝。
                (kb_a, users["outsider"], "viewer"),
            ],
        )
        await connection.executemany(
            """INSERT INTO documents
               (id,knowledge_base_id,name,doc_source,doc_type,status,team,security_level)
               VALUES($1,$2,$3,$4,'md','ready',$5,$6)""",
            [
                (public_doc, kb_a, "公开文档", f"{public_doc}.md", "security", "public"),
                (team_doc, kb_a, "部门文档", f"{team_doc}.md", "platform", "team"),
                (confidential_doc, kb_a, "机密文档", f"{confidential_doc}.md", "platform", "confidential"),
            ],
        )

        def user(name: str, *, team: str = "platform", level: str | None = None) -> dict:
            return {
                "id": users[name],
                "organization_id": org_b if name == "outsider" else org_a,
                "team": team,
                "security_level": level or ("confidential" if name == "admin" else "team"),
            }

        await knowledge_base_service.require_role(connection, kb_a, user("viewer"), "viewer")
        await expect_denied(
            knowledge_base_service.require_role(connection, kb_a, user("viewer"), "editor"),
            403,
            "KNOWLEDGE_BASE_ROLE_REQUIRED",
        )
        await knowledge_base_service.require_role(connection, kb_a, user("editor"), "editor")
        await knowledge_base_service.require_role(connection, kb_a, user("admin"), "admin")
        await expect_denied(
            knowledge_base_service.require_role(connection, kb_a, user("outsider"), "viewer"),
            404,
            "KNOWLEDGE_BASE_NOT_FOUND",
        )
        await expect_denied(
            knowledge_base_service.update_knowledge_base(
                kb_a, {"description": "viewer 越权"}, user("viewer"), "perm-viewer"
            ),
            403,
            "KNOWLEDGE_BASE_ROLE_REQUIRED",
        )
        await expect_denied(
            knowledge_base_service.update_knowledge_base(
                kb_a, {"description": "editor 越权"}, user("editor"), "perm-editor"
            ),
            403,
            "KNOWLEDGE_BASE_ROLE_REQUIRED",
        )
        updated_kb = await knowledge_base_service.update_knowledge_base(
            kb_a, {"description": "admin 已更新"}, user("admin"), "perm-admin"
        )
        assert updated_kb["description"] == "admin 已更新"

        viewer_docs = await knowledge_service.list_documents(user("viewer"), 1, 20)
        assert {row["id"] for row in viewer_docs["items"]} == {public_doc, team_doc}
        other_team_docs = await knowledge_service.list_documents(
            user("viewer", team="security"), 1, 20
        )
        assert {row["id"] for row in other_team_docs["items"]} == {public_doc}
        admin_docs = await rag_service.accessible_documents(user("admin"), [kb_a])
        assert set(admin_docs) == {public_doc, team_doc, confidential_doc}
        await expect_denied(
            knowledge_service.get_document(confidential_doc, user("viewer")),
            404,
            "DOCUMENT_NOT_FOUND",
        )
        await expect_denied(
            document_service.update_document(
                team_doc, {"name": "viewer 越权"}, user("viewer"), "perm-viewer"
            ),
            403,
            "KNOWLEDGE_BASE_ROLE_REQUIRED",
        )
        updated_doc = await document_service.update_document(
            team_doc, {"name": "editor 已更新"}, user("editor"), "perm-editor"
        )
        assert updated_doc["name"] == "editor 已更新"
        await expect_denied(
            rag_service.accessible_knowledge_bases(user("outsider"), [str(kb_a)]),
            404,
            "KNOWLEDGE_BASE_NOT_FOUND",
        )
        print("permission security: organization, role and document clearance boundaries passed")
    finally:
        db.postgres_pool = original_pool
        await transaction.rollback()
        await connection.close()


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _SingleConnectionPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _ConnectionContext(self.connection)


if __name__ == "__main__":
    asyncio.run(main())
