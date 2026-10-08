"""T8 SSO 身份解析与权限注入。

请求头携带 SSO token，按用户团队生成 team/system/security_level 过滤条件。
"""
# TODO(T8 服务组): 权限过滤条件必须强制注入，不可绕过（R9 硬指标）。
