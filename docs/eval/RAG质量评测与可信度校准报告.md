# DevMind AI RAG 质量评测与可信度校准报告

评测日期：2026-08-28  
评测环境：PostgreSQL 16、Milvus、BGE-M3、BGE Reranker、DashScope LLM，本机单实例串行执行。

## 1. 评测范围

- 40 条可复现 Pilot 样例：34 条可回答问题、6 条知识库外问题。
- 覆盖技术排查、权限申请、故障响应、制度规范、工单、意见反馈和跨文档问题。
- 评测真实链路：Query Rewrite → Milvus 混合检索 → Reranker → 动态证据筛选 → LLM 流式生成 → 引用持久化。
- 本轮使用确定性检索、引用和关键词覆盖指标；未引入 RAGAS 或额外 Judge 模型，避免把 Judge 偏差混入阈值校准。

## 2. 基线结果

| 指标 | 结果 |
|---|---:|
| MRR@10 | 0.9412 |
| Recall@5 | 1.0000 |
| Recall@10 | 1.0000 |
| nDCG@5 | 0.9579 |
| Rerank Top-1 准确率 | 1.0000 |
| 检索平均耗时 | 35.70 s |
| 检索最大耗时 | 56.92 s |

原实现用全部重排结果的平均分判断可信度，40 条中有 38 条被判为 Low、2 条 Medium、0 条 High。主要问题不是召回不足，而是低分尾部引用稀释了正确的 Top-1 证据。

## 3. 校准方案

### 3.1 动态证据筛选

1. 重排结果按分数降序排列。
2. Top-1 小于 `0.12` 时视为证据不足，引用清空并拒答。
3. 其余结果同时满足绝对分数 `>= 0.12`、相对分数 `>= Top-1 × 0.45` 才保留。
4. 最多保留 5 条证据，避免弱引用凑数量。

### 3.2 可信度分数

```text
score = 0.75 × Top1
      + 0.15 × Top2
      + 0.10 × Top3
      + 0.05 × (Top1 - Top2)
```

| 等级 | 阈值 | 行为 |
|---|---:|---|
| High | `score >= 0.68` | 正常生成，展示高可信度 |
| Medium | `0.09 <= score < 0.68` | 有证据时谨慎生成 |
| Low | `score < 0.09` 或无有效证据 | 不调用 LLM，返回明确拒答且不展示引用 |

阈值来自本项目 40 条 Pilot 集，不应直接外推到生产语料；语料规模和业务领域变化后需要重新校准。

## 4. 最终真实回答评测

| 指标 | 目标 | 结果 | 结论 |
|---|---:|---:|---|
| 可回答问题正常回答率 | ≥ 90% | 97.06%（33/34） | 通过 |
| 无答案问题拒答率 | ≥ 90% | 100%（6/6） | 通过 |
| 已回答问题引用溯源率 | 100% | 100% | 通过 |
| 引用来源准确率 | ≥ 90% | 96.97% | 通过 |
| High 可信度准确率 | ≥ 90% | 100% | 通过 |
| 答案关键词覆盖率 | ≥ 80% | 86.62% | 通过 |
| 平均端到端回答时长 | < 3 s | 16.98 s | 未通过 |
| p95 端到端回答时长 | < 5 s | 29.77 s | 未通过 |

可信度分布：High 24、Medium 9、Low 7。Low 中包含 6 条知识库外问题和 1 条误拒问题。

## 5. 已知边界

- `policy_005（RAG 是什么）` 的正确文档已被召回，但 Reranker Top-1 仅为约 `0.0005`，低于安全阈值，因此被保守拒答。当前误拒率为 2.94%（1/34）。
- `ticket_001` 的确定性关键词覆盖为 0，但引用来源正确。回答使用了语义等价表述，说明关键词覆盖只能作为低成本代理指标，后续可增加人工标注或独立 Judge 复核。
- 延迟未达标。主要耗时来自本机 CPU 上的 BGE-M3 编码、每题多查询改写和 Reranker；它不影响本轮可信度结论，但在生产上线前必须单独优化和压测。

## 6. 实现变更

- 新增统一可信度模块：`internal_kb_qa/core/confidence.py`。
- 正式工作台与旧 RAG 入口共享同一套证据筛选、可信度和拒答规则。
- 正式回答的 `usage` 与审计日志新增 `confidence_score`、`confidence_reason`、`evidence_count`。
- 引用只持久化经过动态筛选的有效片段。
- 新增 5 条可信度单测及 40 条真实评测脚本。

## 7. 回归结果

- 后端：44 tests passed，4 subtests passed。
- 前端：Vite production build 通过，1816 modules transformed。
- 真实 E2E：注册、知识库、文档上传与索引、检索、流式回答、引用详情、收藏、历史会话和退出登录全部通过。
- E2E 示例：检索 3 个片段，最终保留 1 个引用，可信度 High，生成 299 个字符。

## 8. 产物与复现

- 数据集：`internal_kb_qa/evaluation/rag_quality_set.json`
- 检索基线：`docs/eval/rag_quality_baseline.json`
- 校准对比：`docs/eval/confidence_calibration.json`
- 最终回答结果：`docs/eval/rag_quality_final.json`

```powershell
uv run python -m internal_kb_qa.evaluation.run_quality_eval
uv run python -m internal_kb_qa.evaluation.calibrate_confidence
uv run python -m internal_kb_qa.evaluation.run_answer_eval
uv run pytest -q
uv run python tests/live_e2e.py
```

## 9. 结论与下一步

本阶段已完成 RAG 质量基线、可信度校准、弱证据拒答、动态引用截断和真实链路验证。可信性指标达到 Pilot 验收标准；当前首要剩余风险已经从“回答是否可信”转为“本机推理延迟过高”。下一阶段应优先做模型常驻与批处理、查询改写收敛、GPU 推理或模型服务化，并在此后执行并发压测。
