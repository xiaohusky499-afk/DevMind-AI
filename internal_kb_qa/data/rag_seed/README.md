# RAG 种子语料说明

本目录是「企业内部技术知识库智能问答系统」的 RAG 种子语料，按意图分类组织。

## 文件与意图类别映射

| 文件 | 意图类别 | 文档类型 | 建议团队 |
|------|----------|----------|----------|
| `tech.md` | tech | faq | infra |
| `access_request.md` | access_request | faq | infra |
| `incident.md` | incident | runbook | ops |
| `ticket_inquiry.md` | ticket_inquiry | faq | ops |
| `complaint_suggestion.md` | complaint_suggestion | faq | ops |
| `policy_general.md` | policy_general | wiki | hr |
| `common.md` | common | faq | ops |

## 文档格式

每份文档顶部是元数据块，供 T2 解析器写入 Document metadata：

```text
---
title: ...
category: tech
team: infra
system: internal_tech_kb
doc_type: faq
version: 1.0
security_level: public
last_updated: 2026-08-22
---
```

正文建议用标题、段落和问答对组织，便于 T3 按标题层级切分。

## 使用说明

1. 将本目录文档复制或指向 `internal_kb_qa/data/raw/`
2. 运行文档解析和入库脚本：

```powershell
python -m internal_kb_qa.scripts.ingest_documents --dir internal_kb_qa/data/rag_seed
```

> 当前内容是种子/演示语料，正式上线前请替换或补充真实内部文档。
