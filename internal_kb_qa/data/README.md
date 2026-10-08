# internal_kb_qa/data

- `raw/`：T1 采集的原始内部技术文档（Confluence/Git/接口文档/runbook/复盘报告）
- `processed/`：清洗与脱敏后的标准化文档
- `faq/`：高频 FAQ 表（faq.xlsx）与构建产物
- `desensitization/`：脱敏规则、脱敏记录与权限标注表
- `classify_data/`：T6 意图分类训练语料（SMP2017 + 自标注）
- `open_source/`：LCQMC / DuReader / CMRC 等辅助评测数据

> 所有文档必须带元数据：team、system、doc_type、version、last_updated、security_level。
