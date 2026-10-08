"""DevMind-AI 问答服务入口（T8）。

接口契约以 docs/企业内部技术知识库智能问答系统-任务分工.md 第 T8 节为准：
- GET  /health
- POST /api/create_session
- POST /api/query
- WS   /api/stream
- GET  /api/sources
- POST /api/feedback

当前服务入口占位实现；接口契约见项目文档。
"""
from fastapi import FastAPI

app = FastAPI(title="DevMind-AI 企业内部技术知识库智能问答系统")

# TODO(T8 服务组): 挂载 internal_kb_qa.api 路由、SSO 鉴权、WebSocket 流式输出、
# 转人工/工单联动、用户反馈落库；服务端口统一为 8003。
