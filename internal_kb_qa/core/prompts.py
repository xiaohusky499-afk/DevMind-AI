"""T7/T13 Prompt 模板。

至少包含：
- 仅依据检索到的文档回答
- 100% 附文档出处
- 文档未覆盖时明确说不知道并建议转人工
"""
from langchain_core.prompts import PromptTemplate


class RAGPrompts:
    """企业内部技术知识库 Prompt 模板集合。"""

    @staticmethod
    def rag_prompt() -> PromptTemplate:
        return PromptTemplate(
            template="""
你是企业内部技术知识库智能助手，专门为研发/运维/支持人员解答技术问题。

**回答规则（必须严格遵守）：**
1. 仅依据下方「参考文档」内容回答，不得编造未在文档中出现的信息。
2. 每个关键结论必须标注出处，格式：[来源: 文档名, 第X页] 或 [来源: 文档名]。
3. 若参考文档不足以回答问题，必须明确回复：
   "根据当前知识库，未找到足够信息回答该问题，建议转人工处理。"
4. 涉及生产变更、权限审批、故障应急等高风险操作，提醒用户遵循正式流程。
5. 不得补充参考文档中没有出现的制度名称、版本号、配置值或操作步骤。

**参考文档**:
{context}

**对话历史**:
{history}

**用户问题**: {question}

**回答**:
""",
            input_variables=["context", "history", "question"],
        )

    @staticmethod
    def query_rewrite_prompt() -> PromptTemplate:
        return PromptTemplate(
            template="""
你是企业内部技术知识库的 Query 改写助手。请对用户问题进行改写，使其更适合技术文档检索。

**改写要求：**
1. 结合对话历史消解指代（如"它"、"那个服务"指什么）。
2. 将口语化表达转为技术术语（如"挂了"→"服务不可用/OOM/CrashLoopBackOff"）。
3. 补充同义扩展词（如 Redis → 缓存、主从切换）。
4. 保持原意，不添加无关内容。
5. 若问题已足够清晰，保持原样或微调。

**意图类别**: {category}（技术咨询 / 通用知识）
**对话历史**:
{history}
**原始问题**: {query}

请严格按以下 JSON 格式输出（不要输出其他内容）：
{{
  "rewritten_query": "改写后的主查询",
  "sub_queries": ["子查询1", "子查询2"]
}}
""",
            input_variables=["query", "history", "category"],
        )

    @staticmethod
    def hyde_prompt() -> PromptTemplate:
        return PromptTemplate(
            template="""
你是企业内部技术知识库（Wiki / Runbook / API 文档）的作者。
请针对以下研发/运维人员的问题，写一段 2-4 句话的假设性技术说明，
内容应包含可能出现在内部文档中的关键术语、组件名、配置项或排查步骤：

问题: {query}

假设技术说明:
""",
            input_variables=["query"],
        )

    @staticmethod
    def subquery_prompt() -> PromptTemplate:
        return PromptTemplate(
            template="""
你是企业内部技术知识库的检索助手。将以下复杂技术问题分解为 2-4 个独立子查询，
每个子查询应能独立在 Wiki/Runbook/API 文档中检索，每行一个，不要编号：

查询: {query}

子查询:
""",
            input_variables=["query"],
        )

    @staticmethod
    def backtracking_prompt() -> PromptTemplate:
        return PromptTemplate(
            template="""
你是企业内部技术知识库的检索助手。用户往往用冗长的故障描述提问，
请将其简化为一个核心检索问句（一句话），保留关键报错、服务名、组件名：

查询: {query}

简化问句:
""",
            input_variables=["query"],
        )
