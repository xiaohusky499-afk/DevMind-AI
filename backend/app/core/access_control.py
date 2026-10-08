"""企业知识内容的安全级别判定。"""

SECURITY_LEVEL_RANK = {"public": 0, "team": 1, "confidential": 2}


def can_access_content(current_user: dict, security_level: str, team: str | None) -> bool:
    """判断用户是否可读取内容；调用方仍须先完成组织和知识库成员校验。"""
    user_level = SECURITY_LEVEL_RANK.get(current_user.get("security_level"), 0)
    content_level = SECURITY_LEVEL_RANK.get(security_level, 2)
    if content_level > user_level:
        return False
    return security_level == "public" or not team or team == current_user.get("team")


def document_access_sql(document_alias: str, user_level_param: str, team_param: str) -> str:
    """生成与 ``can_access_content`` 一致的 PostgreSQL 过滤条件。"""
    return f"""(
        CASE {document_alias}.security_level
            WHEN 'public' THEN 0 WHEN 'team' THEN 1 WHEN 'confidential' THEN 2
            ELSE 99 END
        <= CASE {user_level_param}
            WHEN 'public' THEN 0 WHEN 'team' THEN 1 WHEN 'confidential' THEN 2
            ELSE 0 END
        AND ({document_alias}.security_level='public' OR {document_alias}.team IS NULL
             OR {document_alias}.team={team_param})
    )"""
