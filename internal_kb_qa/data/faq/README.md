# FAQ 表结构（T1/T5）

交付物：`faq.xlsx`（或 CSV），建议字段：

| 字段 | 说明 |
|------|------|
| id | FAQ 唯一标识 |
| question | 标准问题 |
| keywords | 关键词/同义词，便于 BM25 |
| answer | 标准答案 |
| source | 文档出处（文件名/链接） |
| category | 环境配置 / 权限申请 / 常见报错等 |
| team | 所属团队 |
| security_level | public / team / confidential |
| version | 版本 |
| last_updated | 更新时间 |
