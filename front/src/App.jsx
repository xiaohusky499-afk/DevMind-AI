import React, { useEffect, useRef, useState } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import {
  ArrowRight,
  Bell,
  Boxes,
  BookOpen,
  Bot,
  Braces,
  ChevronDown,
  CircleHelp,
  Clipboard,
  Copy,
  Database,
  Eye,
  EyeOff,
  FileText,
  GitBranch,
  Heart,
  History,
  Library,
  Link2,
  Mail,
  MessageCircle,
  PanelRight,
  Plus,
  Search,
  Send,
  Settings,
  ShieldCheck,
  ClipboardCheck,
  Smartphone,
  Star,
  Trash2,
  Upload,
  Users,
  Wrench,
  X,
} from "lucide-react";
import {
  authApi,
  chatApi,
  clearAuth,
  documentApi,
  getStoredUser,
  getToken,
  knowledgeBaseApi,
  organizationApi,
  preferencesApi,
  profileApi,
  setAuth,
} from "./api/qa";
import "./styles.css";

gsap.registerPlugin(useGSAP);

function Logo({ compact = false }) {
  return (
    <div className={`logo ${compact ? "compact" : ""}`} aria-label="DevMind AI">
      <svg viewBox="0 0 64 56" aria-hidden="true">
        <path d="M32 8 13 43M32 8l19 35M13 43h38" />
        <circle cx="32" cy="8" r="7" />
        <circle cx="13" cy="43" r="6" />
        <circle cx="51" cy="43" r="6" />
        <circle className="logo-dot" cx="32" cy="8" r="2.5" />
      </svg>
      <span>
        DevMind <b>AI</b>
        <small>INTELLIGENT DEV PARTNER</small>
      </span>
    </div>
  );
}
function GlassButton({ className = "", children, ...props }) {
  return (
    <button className={`glass-button ${className}`} {...props}>
      {children}
    </button>
  );
}
function FormattedAnswer({ children }) {
  return String(children).split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
}
function NavItem({ icon: Icon, children, active, onClick }) {
  return (
    <button onClick={onClick} className={`side-nav ${active ? "active" : ""}`}>
      <Icon size={19} />
      <span>{children}</span>
    </button>
  );
}
function NavLabel({ icon: Icon, children }) {
  return (
    <div className="side-nav side-label">
      <Icon size={19} />
      <span>{children}</span>
    </div>
  );
}
function CitationCard({ item, index, onOpen }) {
  const type = (item.document_type || item.document_name?.split(".").pop() || "DOC")
    .slice(0, 3)
    .toUpperCase();
  return (
    <article
      className="citation-card"
      style={{ animationDelay: `${120 + index * 120}ms` }}
      onClick={() => onOpen?.(item.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (["Enter", " "].includes(event.key)) {
          event.preventDefault();
          onOpen?.(item.id);
        }
      }}
    >
      <div className={`file-icon ${type.toLowerCase()}`}>{type}</div>
      <div className="citation-title">{item.document_name}</div>
      <p className="citation-meta">
        {item.location_label || "文档片段"} <i /> 相关度{" "}
        <strong>{Math.round((item.score || 0) * 100)}%</strong>
      </p>
      <p className="citation-copy">{item.excerpt}</p>
    </article>
  );
}
function RagProgress({ stage, message, retrievedCount = 0, rerankedCount = 0 }) {
  const labels = ["检索", "召回", "重排", "生成"];
  const status = [
    "正在分析问题…",
    "正在检索 12 个知识库…",
    "已找到 13 个相关片段",
    "正在重新排序…",
    "已选取 5 个高相关片段",
  ];
  return (
    <div className="rag-progress">
      <div>
        <Search size={18} />
        <span>
          {message ||
            (stage < 5
              ? status[stage]
              : `已检索 ${retrievedCount} 个相关片段 · 已重排 ${rerankedCount} 个高相关结果`)}
        </span>
      </div>
      <ol>
        {labels.map((label, index) => (
          <li className={stage >= index + 1 ? "done" : ""} key={label}>
            <b>{index === 3 && stage >= 4 ? "✓" : ""}</b>
            <span>{label}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function FeatureOverlay({
  view,
  close,
  onOpenSession,
}) {
  const [query, setQuery] = useState("");
  const [knowledgeBases, setKnowledgeBases] = useState([]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newKnowledgeBase, setNewKnowledgeBase] = useState({ name: "", description: "" });
  const [selectedKnowledgeBase, setSelectedKnowledgeBase] = useState(null);
  const [members, setMembers] = useState([]);
  const [selectedDocument, setSelectedDocument] = useState(null);
  const [documentChunks, setDocumentChunks] = useState([]);
  const [documentTask, setDocumentTask] = useState(null);
  const [documentDraftName, setDocumentDraftName] = useState("");
  const [retrievalKnowledgeBase, setRetrievalKnowledgeBase] = useState("");
  const [topK, setTopK] = useState(20);
  const [rerankTopN, setRerankTopN] = useState(5);
  const [retrievalResult, setRetrievalResult] = useState(null);
  const [filterKeyword, setFilterKeyword] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterType, setFilterType] = useState("");
  const [nextCursor, setNextCursor] = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadTarget, setUploadTarget] = useState("");
  const [knowledgeDraft, setKnowledgeDraft] = useState({ name: "", description: "", status: "ready" });
  const [memberQuery, setMemberQuery] = useState("");
  const [organizationUsers, setOrganizationUsers] = useState([]);
  const [newMemberRole, setNewMemberRole] = useState("viewer");
  const [preferences, setPreferences] = useState({
    default_knowledge_base_ids: [], default_mode: "tech", locale: "zh-CN",
    timezone: "Asia/Shanghai", answer_style: "balanced",
  });
  const fileRef = useRef(null);
  useEffect(() => {
    if (!selectedKnowledgeBase && !selectedDocument) return undefined;
    const closeDetail = (event) => {
      if (event.key !== "Escape") return;
      setSelectedKnowledgeBase(null);
      setSelectedDocument(null);
    };
    document.addEventListener("keydown", closeDetail);
    return () => document.removeEventListener("keydown", closeDetail);
  }, [selectedKnowledgeBase, selectedDocument]);
  const title = {
    knowledge: "知识库",
    documents: "文档管理",
    retrieval: "检索测试",
    history: "历史记录",
    favorites: "我的收藏",
    settings: "系统设置",
  }[view];
  const loadKnowledgeBases = async (keyword = "") => {
    const data = await knowledgeBaseApi.list({ limit: 100, ...(keyword ? { keyword } : {}), ...(view === "knowledge" && filterStatus ? { status: filterStatus } : {}) });
    const nextItems = data.items || [];
    setKnowledgeBases(nextItems);
    if (view === "knowledge") setItems(nextItems);
    if (!retrievalKnowledgeBase && nextItems.length) setRetrievalKnowledgeBase(nextItems[0].id);
    if (!uploadTarget && nextItems.length) setUploadTarget((nextItems.find((item) => item.status === "ready") || nextItems[0]).id);
    return nextItems;
  };
  const loadDocuments = async (keyword = "") => {
    const data = await documentApi.list({ page: 1, page_size: 100, ...(keyword ? { keyword } : {}), ...(filterType ? { source_type: filterType } : {}) });
    setItems(data.items || []);
  };
  const loadHistory = async (cursor = null, append = false) => {
    const data = await chatApi.listSessions({ limit: 20, ...(filterKeyword ? { keyword: filterKeyword } : {}), ...(cursor ? { cursor } : {}) });
    setItems((current) => append ? [...current, ...(data.items || [])] : (data.items || []));
    setNextCursor(data.next_cursor || null);
  };
  const loadFavorites = async (cursor = null, append = false) => {
    const data = await chatApi.listFavorites({ limit: 20, ...(filterKeyword ? { keyword: filterKeyword } : {}), ...(cursor ? { cursor } : {}) });
    setItems((current) => append ? [...current, ...(data.items || [])] : (data.items || []));
    setNextCursor(data.next_cursor || null);
  };
  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      if (["knowledge", "documents", "retrieval", "settings"].includes(view)) {
        await loadKnowledgeBases(view === "knowledge" ? filterKeyword : "");
      }
      if (view === "documents") await loadDocuments(filterKeyword);
      if (view === "history") await loadHistory();
      if (view === "favorites") await loadFavorites();
      if (view === "settings") setPreferences(await preferencesApi.get());
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    setItems([]);
    setError("");
    setNotice("");
    setSelectedKnowledgeBase(null);
    setSelectedDocument(null);
    setRetrievalResult(null);
    setNextCursor(null);
    refresh();
  }, [view]);
  useEffect(() => {
    if (!["knowledge", "documents", "history", "favorites"].includes(view)) return;
    const timer = setTimeout(() => refresh(), 280);
    return () => clearTimeout(timer);
  }, [filterKeyword, filterStatus, filterType]);
  const createKnowledgeBase = async (event) => {
    event.preventDefault();
    if (!newKnowledgeBase.name.trim()) return;
    setLoading(true);
    setError("");
    try {
      await knowledgeBaseApi.create({
        name: newKnowledgeBase.name.trim(),
        description: newKnowledgeBase.description.trim(),
      });
      setNewKnowledgeBase({ name: "", description: "" });
      setCreateOpen(false);
      setNotice("知识库已创建");
      await loadKnowledgeBases();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  };
  const openMembers = async (knowledgeBase) => {
    setSelectedKnowledgeBase(knowledgeBase);
    setKnowledgeDraft({
      name: knowledgeBase.name,
      description: knowledgeBase.description || "",
      status: knowledgeBase.status === "disabled" ? "disabled" : "ready",
    });
    setMemberQuery("");
    setOrganizationUsers([]);
    setSelectedDocument(null);
    setError("");
    try {
      const data = await knowledgeBaseApi.members(knowledgeBase.id);
      setMembers(data.items || []);
    } catch (requestError) {
      setMembers([]);
      setError(requestError.message);
    }
  };
  const saveKnowledgeBase = async (event) => {
    event.preventDefault();
    try {
      const updated = await knowledgeBaseApi.update(selectedKnowledgeBase.id, knowledgeDraft);
      setSelectedKnowledgeBase(updated);
      setNotice("知识库设置已保存");
      await loadKnowledgeBases(filterKeyword);
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const searchOrganizationUsers = async () => {
    try {
      const data = await organizationApi.users({ keyword: memberQuery, limit: 20 });
      const existing = new Set(members.map((member) => member.user_id));
      setOrganizationUsers((data.items || []).filter((user) => !existing.has(user.user_id)));
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const addMember = async (userId) => {
    try {
      await knowledgeBaseApi.setMember(selectedKnowledgeBase.id, userId, newMemberRole);
      await openMembers(selectedKnowledgeBase);
      setNotice("成员已加入知识库");
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const removeMember = async (userId) => {
    try {
      await knowledgeBaseApi.removeMember(selectedKnowledgeBase.id, userId);
      await openMembers(selectedKnowledgeBase);
      setNotice("成员已移除");
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const updateMemberRole = async (userId, role) => {
    try {
      await knowledgeBaseApi.setMember(selectedKnowledgeBase.id, userId, role);
      await openMembers(selectedKnowledgeBase);
      setNotice("成员权限已更新");
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const removeKnowledgeBase = async (knowledgeBase) => {
    if (!window.confirm(`确认归档知识库“${knowledgeBase.name}”吗？`)) return;
    try {
      await knowledgeBaseApi.remove(knowledgeBase.id);
      setSelectedKnowledgeBase(null);
      setNotice("知识库已归档");
      await loadKnowledgeBases();
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const uploadDocument = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const target = knowledgeBases.find((item) => item.id === uploadTarget);
    if (!target) {
      setError("请先创建一个可用知识库");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const uploaded = await documentApi.upload({ file, knowledgeBaseId: target.id });
      setUploadOpen(false);
      setNotice(`“${file.name}”已上传，正在建立索引`);
      await loadDocuments();
      await openDocument({ id: uploaded.id, role: target.role });
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  };
  const openDocument = async (document) => {
    setSelectedDocument(document);
    setSelectedKnowledgeBase(null);
    setError("");
    try {
      const [detail, chunks, task] = await Promise.all([
        documentApi.detail(document.id),
        document.role === "viewer" ? Promise.resolve({ items: [] }) : documentApi.chunks(document.id, { limit: 20 }),
        documentApi.task(document.id).catch(() => null),
      ]);
      setSelectedDocument(detail);
      setDocumentDraftName(detail.name || "");
      setDocumentChunks(chunks.items || []);
      setDocumentTask(task);
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const saveDocument = async () => {
    if (!documentDraftName.trim()) return;
    try {
      const updated = await documentApi.update(selectedDocument.id, { name: documentDraftName.trim() });
      setSelectedDocument((value) => ({ ...value, ...updated }));
      setNotice("文档名称已更新");
      await loadDocuments(filterKeyword);
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const reindexDocument = async (document) => {
    try {
      await documentApi.reindex(document.id);
      setNotice("重新索引任务已提交");
      await loadDocuments();
      await openDocument(document);
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const removeDocument = async (document) => {
    if (!window.confirm(`确认删除文档“${document.name}”吗？`)) return;
    try {
      await documentApi.remove(document.id);
      setSelectedDocument(null);
      setNotice("文档删除任务已提交");
      await loadDocuments();
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const retryDocumentTask = async () => {
    try {
      const task = await documentApi.retryTask(selectedDocument.id);
      setDocumentTask((current) => ({ ...current, ...task, progress: 0, error_message: null }));
      setNotice("失败任务已重新提交");
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  useEffect(() => {
    if (!selectedDocument?.id || !["pending", "running", "retry_wait"].includes(documentTask?.status)) return;
    const timer = setInterval(async () => {
      try {
        const task = await documentApi.task(selectedDocument.id);
        setDocumentTask(task);
        if (["completed", "failed", "dead"].includes(task.status)) {
          await loadDocuments(filterKeyword);
          await openDocument(selectedDocument);
        }
      } catch (requestError) {
        setError(requestError.message);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [selectedDocument?.id, documentTask?.status]);

  const savePreferences = async () => {
    setLoading(true);
    setError("");
    try {
      setPreferences(await preferencesApi.update({
        default_knowledge_base_ids: preferences.default_knowledge_base_ids || [],
        default_mode: preferences.default_mode,
        answer_style: preferences.answer_style,
        locale: preferences.locale,
        timezone: preferences.timezone,
      }));
      setNotice("工作台偏好已保存");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  };
  const removeFavorite = async (messageId) => {
    try {
      await chatApi.unfavorite(messageId);
      setItems((current) => current.filter((item) => item.message_id !== messageId));
      setNotice("已取消收藏");
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const renameSession = async (session) => {
    const title = window.prompt("输入新的会话标题", session.title);
    if (!title?.trim() || title.trim() === session.title) return;
    try {
      const updated = await chatApi.updateSession(session.id, title.trim());
      setItems((current) => current.map((item) => item.id === session.id ? { ...item, ...updated } : item));
      setNotice("会话标题已更新");
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const deleteSession = async (session) => {
    if (!window.confirm(`确认删除会话“${session.title}”吗？`)) return;
    try {
      await chatApi.deleteSession(session.id);
      setItems((current) => current.filter((item) => item.id !== session.id));
      setNotice("会话已删除");
    } catch (requestError) {
      setError(requestError.message);
    }
  };
  const runRetrieval = async (event) => {
    event.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    setRetrievalResult(null);
    try {
      setRetrievalResult(await chatApi.testRetrieval({
        query: query.trim(),
        knowledge_base_ids: retrievalKnowledgeBase ? [retrievalKnowledgeBase] : [],
        top_k: Number(topK),
        rerank_top_n: Number(rerankTopN),
      }));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  };
  const statusLabel = {
    ready: "已就绪", uploaded: "已上传", parsing: "解析中", chunking: "分块中",
    embedding: "向量化", indexing: "索引中", failed: "失败", disabled: "已停用",
  };
  return (
    <section className="feature-overlay">
      <header>
        <div>
          <button className="back" onClick={close}>
            ← 返回智能问答
          </button>
          <h1>{title}</h1>
          <p>
            {view === "retrieval"
              ? "验证召回片段与重排效果，不生成回答。"
              : view === "history"
                ? "选择历史会话，查看其中的问题、回答与引用。"
                : view === "favorites"
                  ? "集中查看已收藏的回答，并返回原始会话继续工作。"
                  : view === "settings"
                    ? "设置默认知识范围、回答方式与界面偏好。"
                  : "管理真实企业知识资产、处理状态与访问权限。"}
          </p>
        </div>
        {view === "documents" && (
          <>
            <input ref={fileRef} className="visually-hidden" type="file" accept=".pdf,.md,.markdown,.docx,.html,.htm,.json" onChange={uploadDocument} />
            <GlassButton className="feature-primary" onClick={() => setUploadOpen(true)} disabled={loading}>
              <Upload /> 上传文档
            </GlassButton>
          </>
        )}
        {view === "knowledge" && (
          <GlassButton className="feature-primary" onClick={() => setCreateOpen(true)}>
            <Plus /> 新建知识库
          </GlassButton>
        )}
      </header>
      {error && <div className="feature-alert error" role="alert">{error}</div>}
      {notice && <div className="feature-alert success" role="status">{notice}</div>}
      {["knowledge", "documents", "history", "favorites"].includes(view) && (
        <div className="feature-filters"><label className="feature-search"><Search /><input value={filterKeyword} onChange={(event) => setFilterKeyword(event.target.value)} placeholder={`搜索${title}`} aria-label={`搜索${title}`} />{filterKeyword && <button onClick={() => setFilterKeyword("")} aria-label="清空搜索"><X /></button>}</label>{view === "knowledge" && <select value={filterStatus} onChange={(event) => setFilterStatus(event.target.value)} aria-label="知识库状态"><option value="">全部状态</option><option value="ready">已启用</option><option value="disabled">已停用</option><option value="failed">失败</option></select>}{view === "documents" && <select value={filterType} onChange={(event) => setFilterType(event.target.value)} aria-label="文档类型"><option value="">全部类型</option><option value="pdf">PDF</option><option value="md">Markdown</option><option value="docx">Word</option><option value="wiki">HTML</option><option value="api">JSON</option></select>}</div>
      )}
      {view === "documents" && uploadOpen && (
        <section className="upload-workflow">
          <div><b>上传并建立索引</b><p>支持 PDF、Markdown、DOCX、HTML 和 JSON，单文件不超过 50 MB。</p></div>
          <label>目标知识库<select value={uploadTarget} onChange={(event) => setUploadTarget(event.target.value)}>{knowledgeBases.filter((item) => item.status === "ready").map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
          <div className="upload-actions"><button onClick={() => setUploadOpen(false)}>取消</button><GlassButton className="feature-primary" onClick={() => fileRef.current?.click()} disabled={!uploadTarget || loading}>选择文件</GlassButton></div>
        </section>
      )}
      {view === "knowledge" && createOpen && (
        <form className="feature-create" onSubmit={createKnowledgeBase}>
          <label>知识库名称<input value={newKnowledgeBase.name} onChange={(event) => setNewKnowledgeBase((value) => ({ ...value, name: event.target.value }))} maxLength={160} autoFocus /></label>
          <label>说明<input value={newKnowledgeBase.description} onChange={(event) => setNewKnowledgeBase((value) => ({ ...value, description: event.target.value }))} maxLength={2000} /></label>
          <div><button type="button" onClick={() => setCreateOpen(false)}>取消</button><GlassButton className="feature-primary" disabled={loading || !newKnowledgeBase.name.trim()}>创建</GlassButton></div>
        </form>
      )}
      {view === "retrieval" ? (
        <form className="retrieval-panel" onSubmit={runRetrieval}>
          <label>
            测试问题
            <textarea value={query} onChange={(e) => setQuery(e.target.value)} placeholder="输入需要验证召回效果的问题" />
          </label>
          <div className="retrieval-controls">
            <label>知识库<select value={retrievalKnowledgeBase} onChange={(event) => setRetrievalKnowledgeBase(event.target.value)}><option value="">全部可访问知识库</option>{knowledgeBases.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            <label>Top K<input type="number" min="1" max="100" value={topK} onChange={(event) => setTopK(event.target.value)} /></label>
            <label>重排结果<input type="number" min="1" max="20" value={rerankTopN} onChange={(event) => setRerankTopN(event.target.value)} /></label>
            <GlassButton className="feature-primary" disabled={loading || !query.trim()}>
              运行检索 <Search />
            </GlassButton>
          </div>
          {loading && <div className="feature-empty">正在执行混合检索与重排…</div>}
          {retrievalResult && <article className="retrieval-result"><div className="retrieval-summary"><span>召回 <b>{retrievalResult.retrieved_count}</b></span><span>重排 <b>{retrievalResult.reranked_count}</b></span><span>耗时 <b>{retrievalResult.latency_ms} ms</b></span></div><small>Query 改写</small><p>{retrievalResult.rewritten_query || retrievalResult.query}</p><ol>{(retrievalResult.results || []).filter((item) => item.stage === "rerank").map((item) => <li key={`${item.chunk_id}-${item.rank}`}><strong>{Number(item.score).toFixed(3)}</strong><div><b>{item.document_name}</b><span>{item.location_label} · 排名 #{item.rank}</span><p>{item.excerpt}</p></div></li>)}</ol></article>}
        </form>
      ) : view === "settings" ? (
        <div className="settings-panel">
          <article>
            <b>默认知识库</b>
            <p>新建会话时优先在这些知识库中检索；未选择时使用全部可访问知识库。</p>
            <div className="settings-choices">{knowledgeBases.map((item) => <label key={item.id}><input type="checkbox" checked={preferences.default_knowledge_base_ids?.includes(item.id)} onChange={(event) => setPreferences((value) => ({ ...value, default_knowledge_base_ids: event.target.checked ? [...value.default_knowledge_base_ids, item.id] : value.default_knowledge_base_ids.filter((id) => id !== item.id) }))} />{item.name}</label>)}</div>
          </article>
          <article>
            <b>默认回答模式</b>
            <p>控制新问题使用的提示模板。</p>
            <select value={preferences.default_mode} onChange={(event) => setPreferences((value) => ({ ...value, default_mode: event.target.value }))}><option value="tech">技术问题</option><option value="troubleshoot">问题排查</option><option value="summarize">文档总结</option></select>
          </article>
          <article>
            <b>回答详略</b>
            <p>决定默认回答的内容密度。</p>
            <select value={preferences.answer_style} onChange={(event) => setPreferences((value) => ({ ...value, answer_style: event.target.value }))}><option value="concise">简洁</option><option value="balanced">均衡</option><option value="detailed">详细</option></select>
          </article>
          <article className="settings-inline"><label>界面语言<select value={preferences.locale} onChange={(event) => setPreferences((value) => ({ ...value, locale: event.target.value }))}><option value="zh-CN">简体中文</option><option value="en-US">English</option></select></label><label>时区<select value={preferences.timezone} onChange={(event) => setPreferences((value) => ({ ...value, timezone: event.target.value }))}><option value="Asia/Shanghai">Asia/Shanghai</option><option value="Asia/Tokyo">Asia/Tokyo</option><option value="America/Los_Angeles">America/Los_Angeles</option></select></label></article>
          <footer><span>偏好保存后会应用到后续新会话。</span><GlassButton className="feature-primary" onClick={savePreferences} disabled={loading}>{loading ? "保存中…" : "保存偏好"}</GlassButton></footer>
        </div>
      ) : view === "favorites" ? (
        loading ? <div className="feature-empty">正在加载收藏…</div> : items.length === 0 ? <div className="feature-empty feature-pending"><Star /><b>还没有收藏回答</b><p>在智能问答中收藏有价值的回答后，会显示在这里。</p></div> : <div className="favorite-list">{items.map((item) => <article key={item.message_id}><div><small>{item.session_title}</small><h3>{item.question || "历史问题"}</h3><p>{item.content}</p><span>{item.citation_count || 0} 个引用 · 可信度 {{ high: "高", medium: "中", low: "低" }[item.confidence] || "未评估"}</span></div><footer><button onClick={() => onOpenSession?.(item.session_id)}>查看会话</button><button className="danger-action" onClick={() => removeFavorite(item.message_id)}>取消收藏</button></footer></article>)}</div>
      ) : view === "history" && loading ? (
        <div className="feature-empty" aria-live="polite">正在加载历史会话…</div>
      ) : view === "history" && error ? (
        <div className="feature-empty feature-error">{error}</div>
      ) : view === "history" && items.length === 0 ? (
        <div className="feature-empty">还没有历史会话，发送第一个问题后会显示在这里。</div>
      ) : loading ? (
        <div className="feature-empty">正在加载数据…</div>
      ) : view !== "history" && items.length === 0 ? (
        <div className="feature-empty">暂无数据，可以从右上角开始创建。</div>
      ) : (
        <div className="feature-grid">
          {items.map((item) => (
            <article
              key={item.id}
              className={view === "history" ? "history-entry" : "feature-entry"}
              onClick={() => view === "history" ? onOpenSession?.(item.id) : view === "knowledge" ? openMembers(item) : openDocument(item)}
              role="button" tabIndex={0}
              onKeyDown={(event) => {
                if (["Enter", " "].includes(event.key)) {
                  event.preventDefault();
                  if (view === "history") onOpenSession?.(item.id);
                  else if (view === "knowledge") openMembers(item);
                  else openDocument(item);
                }
              }}
            >
              <span className="feature-icon">
                {view === "documents"
                  ? (item.source_type || "DOC").slice(0, 3).toUpperCase()
                  : view === "knowledge"
                    ? "KB"
                    : "◷"}
              </span>
              <div>
                <b>{view === "history" ? item.title : item.name}</b>
                <p>
                  {view === "knowledge"
                    ? `${item.document_count || 0} 份文档 · ${item.chunk_count || 0} 个片段 · ${item.member_count || 0} 位成员`
                    : view === "documents"
                      ? `${item.knowledge_base_name} · ${statusLabel[item.status] || item.status} · ${item.chunk_count || 0} 个片段`
                      : view === "history"
                        ? `${item.message_count || 0} 条消息 · ${item.preview || "暂无回答"}`
                        : ""}
                </p>
              </div>
              {view === "knowledge" && <span className={`feature-status ${item.status}`}>{statusLabel[item.status] || item.status}</span>}
              {view === "documents" && <span className={`feature-status ${item.status}`}>{statusLabel[item.status] || item.status}</span>}
              {view === "history" ? <div className="entry-actions"><button onClick={(event) => { event.stopPropagation(); renameSession(item); }}>重命名</button><button className="danger-action" onClick={(event) => { event.stopPropagation(); deleteSession(item); }}>删除</button></div> : <ArrowRight aria-hidden="true" />}
            </article>
          ))}
        </div>
      )}
      {nextCursor && ["history", "favorites"].includes(view) && <button className="load-more" onClick={() => view === "history" ? loadHistory(nextCursor, true) : loadFavorites(nextCursor, true)}>加载更多</button>}
      {(selectedKnowledgeBase || selectedDocument) && (
        <div
          className="feature-modal"
          role="dialog"
          aria-modal="true"
          aria-label={selectedKnowledgeBase ? "知识库详情" : "文档详情"}
          onMouseDown={(event) => {
            if (event.target !== event.currentTarget) return;
            setSelectedKnowledgeBase(null);
            setSelectedDocument(null);
          }}
        >
      {selectedKnowledgeBase && <aside className="feature-detail"><header><div><small>知识库设置</small><h2>{selectedKnowledgeBase.name}</h2></div><button onClick={() => setSelectedKnowledgeBase(null)} aria-label="关闭详情"><X /></button></header><div className="detail-metrics"><span><Database />{selectedKnowledgeBase.document_count || 0} 份文档</span><span><Users />{selectedKnowledgeBase.member_count || 0} 位成员</span></div>{selectedKnowledgeBase.role === "admin" && <form className="detail-form" onSubmit={saveKnowledgeBase}><label>名称<input value={knowledgeDraft.name} onChange={(event) => setKnowledgeDraft((value) => ({ ...value, name: event.target.value }))} /></label><label>说明<textarea value={knowledgeDraft.description} onChange={(event) => setKnowledgeDraft((value) => ({ ...value, description: event.target.value }))} /></label><label>状态<select value={knowledgeDraft.status} onChange={(event) => setKnowledgeDraft((value) => ({ ...value, status: event.target.value }))}><option value="ready">启用</option><option value="disabled">停用</option></select></label><button className="detail-primary">保存知识库设置</button></form>}<h3>成员与角色</h3>{selectedKnowledgeBase.role === "admin" && <div className="member-add"><div><input value={memberQuery} onChange={(event) => setMemberQuery(event.target.value)} placeholder="搜索姓名、账号或部门" onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); searchOrganizationUsers(); } }} /><select value={newMemberRole} onChange={(event) => setNewMemberRole(event.target.value)}><option value="viewer">查看者</option><option value="editor">编辑者</option><option value="admin">管理员</option></select><button onClick={searchOrganizationUsers}><Search /></button></div>{organizationUsers.map((user) => <article key={user.user_id}><div><b>{user.display_name}</b><span>{user.job_title || "未设置职位"} · {user.department_name || "未设置部门"}</span></div><button onClick={() => addMember(user.user_id)}>添加</button></article>)}</div>}<div className="member-list">{members.map((member) => <div key={member.user_id}><div className="member-avatar">{(member.display_name || member.username).slice(0, 1)}</div><div><b>{member.display_name || member.username}</b><span>{member.job_title || "未设置职位"} · {member.department_name || "未设置部门"}</span></div><select value={member.role} disabled={selectedKnowledgeBase.role !== "admin"} onChange={(event) => updateMemberRole(member.user_id, event.target.value)}><option value="viewer">查看者</option><option value="editor">编辑者</option><option value="admin">管理员</option></select>{selectedKnowledgeBase.role === "admin" && <button className="member-remove" onClick={() => removeMember(member.user_id)} aria-label={`移除${member.display_name || member.username}`}><X /></button>}</div>)}</div>{selectedKnowledgeBase.role === "admin" && <footer><button className="danger-action" onClick={() => removeKnowledgeBase(selectedKnowledgeBase)}><Trash2 />归档知识库</button></footer>}</aside>}
      {selectedDocument && <aside className="feature-detail"><header><div><small>文档详情</small><h2>{selectedDocument.name}</h2></div><button onClick={() => setSelectedDocument(null)} aria-label="关闭详情"><X /></button></header><div className="detail-metrics"><span>{selectedDocument.source_type?.toUpperCase()} · {(selectedDocument.size_bytes / 1024).toFixed(1)} KB</span><span>{selectedDocument.chunk_count || 0} 个片段</span></div>{selectedDocument.role !== "viewer" && <div className="document-rename"><input value={documentDraftName} onChange={(event) => setDocumentDraftName(event.target.value)} aria-label="文档显示名称" /><button onClick={saveDocument}>保存名称</button></div>}{documentTask && <div className="task-progress"><div><span>最近任务：{documentTask.task_type}{documentTask.status === "retry_wait" ? " · 等待自动重试" : ""}</span><b>{documentTask.progress}%</b></div><i><b style={{ width: `${documentTask.progress}%` }} /></i>{documentTask.error_message && <p>{documentTask.error_message}</p>}{["failed", "dead"].includes(documentTask.status) && selectedDocument.role !== "viewer" && <button onClick={retryDocumentTask}>重新执行任务</button>}</div>}<h3>内容片段</h3><div className="chunk-list">{documentChunks.length ? documentChunks.map((chunk) => <article key={chunk.id}><b>{chunk.location_label}</b><span>{chunk.indexed ? "已索引" : "未索引"}</span><p>{chunk.content}</p></article>) : <p className="detail-muted">当前角色不可查看分块，或文档尚未完成索引。</p>}</div>{selectedDocument.role !== "viewer" && <footer><button onClick={() => reindexDocument(selectedDocument)}>重新索引</button><button className="danger-action" onClick={() => removeDocument(selectedDocument)}><Trash2 />删除文档</button></footer>}</aside>}
        </div>
      )}
    </section>
  );
}

function ProfileOverlay({ profile, onSave, onSendPhoneCode, close }) {
  const [draft, setDraft] = useState(profile);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [phoneCode, setPhoneCode] = useState("");
  const [codeMessage, setCodeMessage] = useState("");
  useEffect(() => {
    setDraft(profile);
    setPhoneCode("");
  }, [profile]);
  const editable = new Set(profile.editable_fields || []);
  const canEdit = (field) => editable.size === 0 || editable.has(field);
  const change = (field) => (event) => {
    setDraft((value) => ({ ...value, [field]: event.target.value }));
    setSaved(false);
    setError("");
    if (field === "phone") {
      setPhoneCode("");
      setCodeMessage("");
    }
  };
  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    const fields = [
      "display_name", "avatar_url", "gender", "birth_date", "email", "phone",
      "department_name", "job_title", "employee_no", "joined_at", "bio",
      "timezone", "locale",
    ];
    const payload = Object.fromEntries(
      fields.filter((field) => canEdit(field)).map((field) => [field, draft[field] || null]),
    );
    if (draft.phone !== profile.phone) payload.phone_verification_code = phoneCode;
    try {
      await onSave(payload);
      setSaved(true);
      setPhoneCode("");
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };
  const sendPhoneCode = async () => {
    setCodeMessage("");
    setError("");
    try {
      const data = await onSendPhoneCode(draft.phone);
      setCodeMessage(data.debug_code ? `开发环境验证码：${data.debug_code}` : "验证码已发送");
    } catch (sendError) {
      setError(sendError.message);
    }
  };
  return (
    <section className="feature-overlay profile-overlay">
      <header>
        <div>
          <button className="back" onClick={close}>
            ← 返回智能问答
          </button>
          <h1>个人信息</h1>
          <p>完善个人档案，帮助团队成员快速识别你的角色与协作方式。</p>
        </div>
      </header>
      <form className="profile-editor" onSubmit={save}>
        <aside className="profile-summary-card">
          <div className="profile-summary-identity">
            <div className="profile-summary-avatar">{draft.display_name?.slice(0, 1) || "张"}</div>
            <div>
              <h2>{draft.display_name || "未填写姓名"}</h2>
              <p>{draft.job_title || "未填写职位"} · {draft.department_name || "未填写部门"}</p>
            </div>
          </div>
          <div className="profile-summary-status"><i /> 账户资料完整度良好</div>
          <div className="profile-summary-facts">
            <div><Mail /><span>企业邮箱</span><b>{draft.email || "待填写"}</b></div>
            <div><Smartphone /><span>手机号</span><b>{draft.phone || "待填写"}</b></div>
            <div><ShieldCheck /><span>工号</span><b>{draft.employee_no || "待填写"}</b></div>
          </div>
        </aside>
        <div className="profile-editor-grid">
          <div className="profile-form-panels">
            <section className="profile-form-panel">
              <div className="profile-panel-heading">
                <h2>基础资料</h2>
                <p>这些信息将用于工作台中的个人身份展示。</p>
              </div>
              <div className="profile-form-grid">
                <label>
                  姓名
                  <input value={draft.display_name || ""} onChange={change("display_name")} disabled={!canEdit("display_name")} required />
                </label>
                <fieldset className="profile-gender">
                  <legend>性别</legend>
                  {[["male", "男"], ["female", "女"], ["unspecified", "不透露"]].map(([value, label]) => (
                    <label key={value} className={draft.gender === value ? "selected" : ""}>
                      <input type="radio" name="gender" value={value} checked={draft.gender === value} onChange={change("gender")} disabled={!canEdit("gender")} />
                      {label}
                    </label>
                  ))}
                </fieldset>
                <label>
                  出生日期
                  <input type="date" value={draft.birth_date || ""} onChange={change("birth_date")} disabled={!canEdit("birth_date")} />
                </label>
                <label>
                  手机号
                  <input type="tel" value={draft.phone || ""} onChange={change("phone")} disabled={!canEdit("phone")} placeholder="请输入 E.164 手机号，例如 +8613800138000" />
                </label>
                {draft.phone !== profile.phone && canEdit("phone") && (
                  <label className="profile-span-2">
                    新手机号验证码
                    <span className="profile-code-field">
                      <input value={phoneCode} onChange={(event) => setPhoneCode(event.target.value)} placeholder="请输入验证码" />
                      <button type="button" onClick={sendPhoneCode}>获取验证码</button>
                    </span>
                    {codeMessage && <small className="profile-code-message">{codeMessage}</small>}
                  </label>
                )}
                <label className="profile-span-2">
                  企业邮箱
                  <input type="email" value={draft.email || ""} onChange={change("email")} disabled={!canEdit("email")} placeholder="邮箱修改需完成验证" />
                </label>
              </div>
            </section>
            <section className="profile-form-panel">
              <div className="profile-panel-heading">
                <h2>工作信息</h2>
                <p>清晰的团队信息会同步展示在侧栏与个人资料中。</p>
              </div>
              <div className="profile-form-grid">
                <label>
                  部门
                  <input value={draft.department_name || ""} onChange={change("department_name")} disabled={!canEdit("department_name")} required />
                </label>
                <label>
                  职位
                  <input value={draft.job_title || ""} onChange={change("job_title")} disabled={!canEdit("job_title")} required />
                </label>
                <label>
                  工号
                  <input value={draft.employee_no || ""} onChange={change("employee_no")} disabled={!canEdit("employee_no")} />
                </label>
                <label>
                  入职日期
                  <input type="date" value={draft.joined_at || ""} onChange={change("joined_at")} disabled={!canEdit("joined_at")} />
                </label>
                <label className="profile-span-2">
                  个人简介
                  <textarea value={draft.bio || ""} onChange={change("bio")} disabled={!canEdit("bio")} placeholder="介绍你的专业方向与协作偏好" />
                </label>
              </div>
            </section>
            <section className="profile-form-panel profile-preferences-panel">
              <div className="profile-panel-heading">
                <h2>使用偏好</h2>
              </div>
              <div className="profile-form-grid">
                <label>
                  时区
                  <select value={draft.timezone || "Asia/Shanghai"} onChange={change("timezone")} disabled={!canEdit("timezone")}>
                    <option value="Asia/Shanghai">中国标准时间（UTC+8）</option>
                    <option value="Asia/Tokyo">日本标准时间（UTC+9）</option>
                    <option value="America/Los_Angeles">太平洋时间</option>
                  </select>
                </label>
                <label>
                  界面语言
                  <select value={draft.locale || "zh-CN"} onChange={change("locale")} disabled={!canEdit("locale")}>
                    <option value="zh-CN">简体中文</option>
                    <option value="en-US">English</option>
                  </select>
                </label>
              </div>
            </section>
          </div>
        </div>
        <footer className="profile-form-actions">
          <span className={error ? "profile-save-error" : ""}>{error || (saved ? "资料已保存，并已同步至侧栏" : "修改后请点击保存资料")}</span>
          <div>
            <button type="button" onClick={() => { setDraft(profile); setSaved(false); }}>重置</button>
            <GlassButton className="feature-primary" type="submit" disabled={saving}>{saving ? "保存中…" : saved ? "已保存 ✓" : "保存资料"}</GlassButton>
          </div>
        </footer>
      </form>
    </section>
  );
}

function normalizeProfile(data = {}) {
  const stored = getStoredUser() || {};
  return {
    display_name: data.display_name || stored.nickname || stored.username || "未设置姓名",
    avatar_url: data.avatar_url || null,
    gender: data.gender || "unspecified",
    birth_date: data.birth_date || "",
    email: data.email || "",
    phone: data.phone || stored.phone || "",
    department_name: data.department_name || "",
    job_title: data.job_title || "",
    employee_no: data.employee_no || "",
    joined_at: data.joined_at || "",
    bio: data.bio || "",
    timezone: data.timezone || "Asia/Shanghai",
    locale: data.locale || "zh-CN",
    organization: data.organization || null,
    editable_fields: data.editable_fields || [],
  };
}

function Workspace({ onLanding, onLogout }) {
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [stage, setStage] = useState(0);
  const [ragMessage, setRagMessage] = useState("");
  const [retrievedCount, setRetrievedCount] = useState(0);
  const [rerankedCount, setRerankedCount] = useState(0);
  const [citationItems, setCitationItems] = useState([]);
  const [selectedCitation, setSelectedCitation] = useState(null);
  const [confidence, setConfidence] = useState(null);
  const [ragError, setRagError] = useState("");
  const [progressVisible, setProgressVisible] = useState(false);
  const [working, setWorking] = useState(false);
  const [mode, setMode] = useState("tech");
  const [defaultKnowledgeBaseIds, setDefaultKnowledgeBaseIds] = useState([]);
  const [favoriteIds, setFavoriteIds] = useState(() => new Set());
  const [knowledgeBaseCount, setKnowledgeBaseCount] = useState(0);
  const streamSocketRef = useRef(null);
  const promptRef = useRef(null);
  const [sideOpen, setSideOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(
    () => window.matchMedia("(min-width: 901px)").matches,
  );
  const [view, setView] = useState("chat");
  const [copied, setCopied] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profile, setProfile] = useState(() => normalizeProfile());
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileLoadError, setProfileLoadError] = useState("");
  const loadProfile = async () => {
    setProfileLoading(true);
    setProfileLoadError("");
    try {
      setProfile(normalizeProfile(await profileApi.get()));
    } catch (error) {
      if (error.status === 401) onLogout();
      else setProfileLoadError(error.message);
    } finally {
      setProfileLoading(false);
    }
  };
  const loadWorkspacePreferences = async () => {
    try {
      const value = await preferencesApi.get();
      setMode(value.default_mode || "tech");
      setDefaultKnowledgeBaseIds(value.default_knowledge_base_ids || []);
    } catch (error) {
      if (error.status === 401) onLogout();
    }
  };
  const loadFavoriteIds = async () => {
    try {
      const data = await chatApi.listFavorites({ limit: 100 });
      setFavoriteIds(new Set((data.items || []).map((item) => item.message_id)));
    } catch (error) {
      if (error.status === 401) onLogout();
    }
  };
  useEffect(() => {
    loadProfile();
    loadWorkspacePreferences();
    loadFavoriteIds();
    knowledgeBaseApi
      .list({ status: "ready", limit: 100 })
      .then((data) => setKnowledgeBaseCount(data.items?.length || 0))
      .catch((error) => {
        if (error.status === 401) onLogout();
      });
    return () => {
      if (!streamSocketRef.current) return;
      streamSocketRef.current.onclose = null;
      streamSocketRef.current.close();
    };
  }, []);
  useEffect(() => {
    if (view === "chat") {
      loadWorkspacePreferences();
      loadFavoriteIds();
    }
  }, [view]);
  const saveProfile = async (payload) => {
    const savedProfile = normalizeProfile(await profileApi.update(payload));
    setProfile(savedProfile);
    return savedProfile;
  };
  const patchMessage = (id, patch) =>
    setMessages((items) =>
      items.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    );
  const toggleFavorite = async (messageId) => {
    const isFavorite = favoriteIds.has(messageId);
    try {
      if (isFavorite) await chatApi.unfavorite(messageId);
      else await chatApi.favorite(messageId);
      setFavoriteIds((current) => {
        const next = new Set(current);
        if (isFavorite) next.delete(messageId);
        else next.add(messageId);
        return next;
      });
    } catch (error) {
      setRagError(error.message);
    }
  };
  const showCitations = async (messageId) => {
    setSourceOpen(true);
    try {
      const data = await chatApi.getCitations(messageId);
      setCitationItems(data.items || []);
      setConfidence(data.confidence || null);
    } catch (error) {
      setRagError(error.message);
    }
  };
  const openCitation = async (citationId) => {
    setRagError("");
    try {
      setSelectedCitation(await chatApi.getCitation(citationId));
    } catch (error) {
      setRagError(error.message);
    }
  };
  const loadSession = async (id) => {
    setRagError("");
    try {
      const data = await chatApi.getSession(id);
      streamSocketRef.current?.close();
      setSessionId(data.id);
      setMessages(data.messages || []);
      setView("chat");
      setWorking(false);
      setProgressVisible(false);
      setRagError("");
      setStage(5);
      const latestAnswer = [...(data.messages || [])]
        .reverse()
        .find((item) => item.role === "assistant" && item.status === "completed");
      if (latestAnswer) await showCitations(latestAnswer.id);
      else {
        setCitationItems([]);
        setConfidence(null);
      }
    } catch (error) {
      setRagError(error.message);
    }
  };
  const ask = async (textOverride) => {
    const text =
      typeof textOverride === "string" ? textOverride.trim() : prompt.trim();
    if (!text || working) return;
    streamSocketRef.current?.close();
    const optimisticUserId = `user-${crypto.randomUUID()}`;
    const optimisticAssistantId = `assistant-${crypto.randomUUID()}`;
    setMessages((items) => [
      ...items,
      { id: optimisticUserId, role: "user", content: text, status: "completed" },
      { id: optimisticAssistantId, role: "assistant", content: "", status: "pending" },
    ]);
    setPrompt("");
    setWorking(true);
    setProgressVisible(true);
    setStage(0);
    setRagMessage("正在分析问题…");
    setRetrievedCount(0);
    setRerankedCount(0);
    setCitationItems([]);
    setConfidence(null);
    setRagError("");
    let assistantId = optimisticAssistantId;
    try {
      let currentSessionId = sessionId;
      if (!currentSessionId) {
        const session = await chatApi.createSession({
          title: "",
          knowledge_base_ids: defaultKnowledgeBaseIds,
        });
        currentSessionId = session.id;
        setSessionId(session.id);
      }
      const created = await chatApi.createMessage({
        session_id: currentSessionId,
        content: text,
        mode,
        knowledge_base_ids: defaultKnowledgeBaseIds,
        parent_message_id: null,
      });
      assistantId = created.assistant_message_id;
      setMessages((items) =>
        items.map((item) =>
          item.id === optimisticUserId
            ? { ...item, id: created.user_message_id }
            : item.id === optimisticAssistantId
              ? { ...item, id: assistantId, status: "running" }
              : item,
        ),
      );
      let finished = false;
      const socket = chatApi.connectStream(created.stream_url, (event) => {
        const data = event.data || {};
        if (event.type === "status") {
          setStage(0);
          setRagMessage(data.message || "正在分析问题…");
        } else if (event.type === "retrieval") {
          setRagMessage(data.message || "正在检索知识库…");
          setRetrievedCount(data.retrieved_count || 0);
          setRerankedCount(data.reranked_count || 0);
          setStage(data.stage === "rerank" ? 3 : 2);
        } else if (event.type === "citations") {
          setCitationItems(data.items || []);
          setStage(4);
          setRagMessage("正在生成回答…");
        } else if (event.type === "token") {
          setStage(4);
          setMessages((items) =>
            items.map((item) =>
              item.id === assistantId
                ? { ...item, content: `${item.content || ""}${data.content || ""}` }
                : item,
            ),
          );
        } else if (event.type === "final") {
          finished = true;
          patchMessage(assistantId, {
            status: "completed",
            confidence: data.confidence,
            citation_count: data.citation_count,
          });
          setConfidence(data.confidence || null);
          setStage(5);
          setRagMessage("");
          setWorking(false);
        } else if (event.type === "error") {
          finished = true;
          patchMessage(assistantId, { status: "failed", error_code: data.code });
          setRagError(data.message || "回答生成失败，请重试。");
          setWorking(false);
          setProgressVisible(false);
        }
      });
      streamSocketRef.current = socket;
      socket.onerror = () => setRagError("流式连接异常，正在检查已保存的回答…");
      socket.onclose = async () => {
        if (finished) return;
        try {
          const saved = await chatApi.getMessage(assistantId);
          patchMessage(assistantId, saved);
          if (saved.status === "completed") {
            await showCitations(assistantId);
            setStage(5);
            setRagError("");
          } else {
            setRagError("回答生成已中断，请重新发送问题。");
            setProgressVisible(false);
          }
        } catch (error) {
          setRagError(error.message);
        } finally {
          setWorking(false);
        }
      };
    } catch (error) {
      patchMessage(assistantId, {
        status: "failed",
        content: error.message,
      });
      setRagError(error.message);
      setWorking(false);
      setProgressVisible(false);
      if (error.status === 401) onLogout();
    }
  };
  const copyAnswer = async (content) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };
  const sidebar = (
    <aside className={`sidebar ${sideOpen ? "shown" : ""}`}>
      <div className="sidebar-top">
        <Logo />
        <button
          className="mobile-close"
          onClick={() => setSideOpen(false)}
          aria-label="关闭菜单"
        >
          <X />
        </button>
      </div>
      <GlassButton
        className="new-chat"
        onClick={() => {
          streamSocketRef.current?.close();
          setView("chat");
          setPrompt("");
          setMessages([]);
          setSessionId(null);
          setCitationItems([]);
          setConfidence(null);
          setRagError("");
          setWorking(false);
          setProgressVisible(false);
        }}
      >
        <Plus /> 新建对话
      </GlassButton>
      <nav>
        <NavLabel icon={PanelRight}>AI 工作台</NavLabel>
        <NavItem
          icon={MessageCircle}
          active={view === "chat"}
          onClick={() => setView("chat")}
        >
          智能问答
        </NavItem>
        <NavItem
          icon={Library}
          active={view === "knowledge"}
          onClick={() => setView("knowledge")}
        >
          知识库
        </NavItem>
        <NavItem
          icon={FileText}
          active={view === "documents"}
          onClick={() => setView("documents")}
        >
          文档管理
        </NavItem>
        <NavItem
          icon={Search}
          active={view === "retrieval"}
          onClick={() => setView("retrieval")}
        >
          检索测试
        </NavItem>
        <span className="nav-label">历史记录</span>
        <NavItem
          icon={History}
          active={view === "history"}
          onClick={() => setView("history")}
        >
          历史记录
        </NavItem>
        <NavItem
          icon={Star}
          active={view === "favorites"}
          onClick={() => setView("favorites")}
        >
          我的收藏
        </NavItem>
        <span className="nav-label">系统</span>
        <NavItem
          icon={Settings}
          active={view === "settings"}
          onClick={() => setView("settings")}
        >
          系统设置
        </NavItem>
      </nav>
      <div className="profile-wrap">
        <button
          className="profile"
          onClick={() => setProfileOpen((value) => !value)}
        >
          <div className="avatar">{profile.display_name.slice(0, 1) || "张"}</div>
          <div className="profile-meta">
            <b>{profile.display_name}</b>
            <span>{profile.job_title || "未设置职位"} · {profile.department_name || "未设置部门"}</span>
          </div>
          <ChevronDown size={17} />
        </button>
        {profileOpen && (
          <div className="profile-menu">
            <div className="profile-menu-summary">
              <div className="avatar">{profile.display_name.slice(0, 1) || "张"}</div>
              <div>
                <b>{profile.display_name}</b>
                <span>{profile.job_title || "未设置职位"}</span>
                <small>{profile.department_name || "未设置部门"}</small>
              </div>
            </div>
            <button
              onClick={() => {
                setView("profile");
                setProfileOpen(false);
              }}
            >
              查看个人信息
            </button>
            <button className="profile-logout" onClick={onLogout}>
              退出登录
            </button>
          </div>
        )}
      </div>
    </aside>
  );
  const sources = (
    <aside className={`citations ${sourceOpen ? "shown" : ""}`}>
      <header>
        <div>
          <h2>引用来源</h2>
          <p>每个结论均可溯源至原文</p>
        </div>
        <button
          className="mobile-close"
          onClick={() => setSourceOpen(false)}
          aria-label="关闭引用"
        >
          <X />
        </button>
      </header>
      {citationItems.length ? (
        citationItems.map((item, index) => (
          <CitationCard key={item.id} item={item} index={index} onOpen={openCitation} />
        ))
      ) : (
        <div className="citation-empty">
          <BookOpen />
          <b>等待回答引用</b>
          <p>发送问题后，命中的知识片段会显示在这里。</p>
        </div>
      )}
      {citationItems.length > 0 && (
        <div className="trust">
          <ShieldCheck />
          <div>
            <b>回答依据 {citationItems.length} 个知识片段</b>
            <span>
              可信度　<strong>{{ high: "高", medium: "中", low: "低" }[confidence] || "评估中"}</strong>
            </span>
          </div>
        </div>
      )}
      {selectedCitation && (
        <div className="citation-detail">
          <header><div><small>引用原文</small><b>{selectedCitation.document_name}</b></div><button onClick={() => setSelectedCitation(null)} aria-label="关闭引用详情"><X /></button></header>
          <span>{selectedCitation.location_label}</span>
          <p>{selectedCitation.content}</p>
        </div>
      )}
    </aside>
  );
  const retryQuestion = [...messages]
    .reverse()
    .find((message) => message.role === "user")?.content;
  return (
    <div className={`workspace-shell ${sourceOpen ? "" : "citations-hidden"}`}>
      <button
        className="mobile-trigger left"
        onClick={() => setSideOpen(true)}
        aria-label="打开菜单"
      >
        <PanelRight />
      </button>
      {sidebar}
      <main className="workspace-main">
        <header className="workspace-header">
          <div>
            <p className="back" onClick={onLanding}>
              ← 返回展示页
            </p>
            <h1>企业技术知识助手</h1>
          </div>
          <div className="top-actions">
            <span className="connection">
              <i /> 已连接 {knowledgeBaseCount} 个知识库
            </span>
            <button
              className="citation-toggle"
              onClick={() => setSourceOpen((value) => !value)}
              aria-label={sourceOpen ? "隐藏引用来源" : "打开引用来源"}
              aria-pressed={sourceOpen}
            >
              <PanelRight />
            </button>
            <Bell />
            <CircleHelp />
          </div>
        </header>
        <div className={`workspace-context ${messages.length === 0 && !working ? "is-empty" : ""}`}>
          {messages.length === 0 && !working && (
            <>
              <section className="greeting">
                <div className="greeting-orbit">
                  <span />
                  <span />
                  <span />
                </div>
                <h2>👋 晚上好，今天想了解什么？</h2>
                <p>连接企业文档、Wiki 与代码仓库，获取带依据的技术答案。</p>
              </section>
              <section className="quick-actions">
                {[
                  [MessageCircle, "技术问题", "解答架构、原理与实现等技术问题"],
                  [Wrench, "问题排查", "协助定位与分析系统或业务问题"],
                  [Clipboard, "文档总结", "提炼文档要点，生成结构化摘要"],
                ].map(([Icon, title, copy], index) => (
                  <button
                    key={title}
                    onClick={() => {
                      setMode(["tech", "troubleshoot", "summarize"][index]);
                      setPrompt(
                        [
                          "请描述你想了解的技术架构或实现问题",
                          "请描述故障现象、报错信息和已尝试的操作",
                          "请说明需要总结的文档或知识主题",
                        ][index],
                      );
                    }}
                  >
                    <span className={`action-icon ${title}`}>
                      <Icon />
                    </span>
                    <div>
                      <b>{title}</b>
                      <p>{copy}</p>
                    </div>
                    <ArrowRight />
                  </button>
                ))}
              </section>
            </>
          )}
          <section className="conversation">
            {messages.map((message) =>
              message.role === "user" ? (
                <div className="user-message" key={message.id}>{message.content}</div>
              ) : (
                <div className="answer-row" key={message.id}>
                  <div className="bot-avatar"><Bot size={19} /></div>
                  <article className={`answer-card ${message.status === "failed" ? "failed" : ""}`}>
                    <p className="answer-text">
                      <FormattedAnswer>{message.content ||
                        (message.status === "failed"
                          ? "回答生成失败，请重新发送问题。"
                          : "正在为你整理企业内部知识…")}</FormattedAnswer>
                      {message.status === "running" && <i className="typing" />}
                    </p>
                    {message.status === "completed" && message.content && (
                      <div className="answer-actions">
                        <GlassButton onClick={() => copyAnswer(message.content)}>
                          <Copy />{copied ? "已复制" : "复制"}
                        </GlassButton>
                        <GlassButton onClick={() => {
                          setPrompt("基于刚才的回答，请继续说明企业落地时的注意事项。");
                          window.setTimeout(() => promptRef.current?.focus(), 0);
                        }}>
                          <MessageCircle />继续追问
                        </GlassButton>
                        <GlassButton onClick={() => showCitations(message.id)}>
                          <BookOpen />查看引用
                        </GlassButton>
                        <GlassButton onClick={() => toggleFavorite(message.id)} aria-pressed={favoriteIds.has(message.id)}>
                          <Heart fill={favoriteIds.has(message.id) ? "currentColor" : "none"} />
                          {favoriteIds.has(message.id) ? "已收藏" : "收藏"}
                        </GlassButton>
                      </div>
                    )}
                  </article>
                </div>
              ),
            )}
          </section>
          {progressVisible && messages.length > 0 && (
            <RagProgress
              stage={stage}
              message={ragMessage}
              retrievedCount={retrievedCount}
              rerankedCount={rerankedCount}
            />
          )}
          {ragError && (
            <div className="rag-error" role="alert">
              <span>{ragError}</span>
              {retryQuestion && !working && (
                <button onClick={() => ask(retryQuestion)}>重新发送</button>
              )}
            </div>
          )}
        </div>
        <section className="workspace-composer">
          <div className="question-box">
            <textarea
              ref={promptRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  ask();
                }
              }}
              placeholder="例如：Milvus 为什么适合企业级 RAG？"
              aria-label="技术问题"
            />
            <button onClick={() => ask()} disabled={working || !prompt.trim()} aria-label="发送问题">
              <Send size={20} />
            </button>
          </div>
        </section>
      </main>
      {view === "profile" ? (
        profileLoading ? (
          <section className="feature-overlay profile-status"><p>正在加载个人资料…</p></section>
        ) : profileLoadError ? (
          <section className="feature-overlay profile-status">
            <p>{profileLoadError}</p>
            <GlassButton className="feature-primary" onClick={loadProfile}>重新加载</GlassButton>
          </section>
        ) : (
          <ProfileOverlay
            profile={profile}
            onSave={saveProfile}
            onSendPhoneCode={authApi.sendCode}
            close={() => setView("chat")}
          />
        )
      ) : (
        view !== "chat" && (
          <FeatureOverlay
            view={view}
            close={() => setView("chat")}
            onOpenSession={loadSession}
          />
        )
      )}
      <button
        className="mobile-trigger right"
        onClick={() => setSourceOpen(true)}
        aria-label="打开引用"
      >
        <BookOpen />
      </button>
      {sources}
    </div>
  );
}
function LegacyLanding({ onEnter, onLogin }) {
  return (
    <div className="landing landing-reference">
      <section className="landing-left">
        <header className="reference-brand">
          <Logo />
          <span className="deploy-tag">企业级 · 私有部署</span>
        </header>
        <main className="reference-copy">
          <p>Enterprise AI Knowledge Workspace</p>
          <h1>
            让企业技术知识
            <br />
            真正可检索
            <br />
            <em>可追溯、可回答</em>
          </h1>
          <div className="copy-detail">
            连接内部文档、Wiki、代码仓库与技术规范，
            <br />
            让可信检索、权限控制与引用溯源贯穿每次回答。
          </div>
          <div className="feature-tags">
            <span>私有化部署</span>
            <span>细粒度权限</span>
            <span>全链路溯源</span>
          </div>
          <div className="reference-cta">
            <GlassButton className="primary" onClick={onEnter}>
              进入 DevMind AI <ArrowRight />
            </GlassButton>
            <button className="watch">
              <span>▶</span>观看演示
            </button>
          </div>
        </main>
        <footer className="reference-foot">
          <span>企业知识基础设施</span>
          <div>
            <b>SSO / RBAC</b>
            <b>审计日志</b>
            <b>多知识库隔离</b>
          </div>
          <small>PDF　WIKI　GIT　API　DATABASE</small>
        </footer>
      </section>
      <section className="landing-right">
        <header className="reference-nav">
          <nav>
            <a>工作空间</a>
            <a>知识库</a>
            <a>安全与权限</a>
            <a>开发者</a>
          </nav>
          <GlassButton onClick={onLogin}>
            登录 <ArrowRight size={15} />
          </GlassButton>
        </header>
        <div className="ambient-orbit" />
        <i className="network-dot d1" />
        <i className="network-dot d2" />
        <i className="network-dot d3" />
        <div className="preview-shell">
          <div className="preview-app">
            <header className="preview-header">
              <Logo compact />
              <span>
                <i /> 已连接 12 个知识库
              </span>
              <div className="preview-avatar">张</div>
            </header>
            <aside className="preview-side">
              <button>＋ 新建对话</button>
              <b>◉　智能问答</b>
              <span>◫　知识库</span>
              <span>▤　文档管理</span>
              <span>⌕　检索测试</span>
              <span>◷　历史记录</span>
              <span>☆　我的收藏</span>
            </aside>
            <main className="preview-main">
              <h2>企业技术知识助手</h2>
              <p>基于内部知识库回答 · 自动检索、重排并引用原文</p>
              <div className="preview-question">
                生产环境如何配置统一身份认证？<i>➤</i>
              </div>
              <div className="preview-rag">
                <small>RAG 检索链路</small>
                <div>
                  <b />
                  检索 <i /> <b />
                  召回 <i /> <b />
                  重排 <i /> <b className="ready">✓</b>生成
                </div>
                <strong>13 片段 → 5 高相关结果</strong>
              </div>
              <article className="preview-answer">
                <b>DevMind AI</b>
                <p>
                  根据《统一身份认证接入规范》与《生产环境部署手册》，推荐采用
                  OIDC + 企业 SSO：
                </p>
                <span>• 身份源：接入企业 IdP，并启用 MFA。</span>
                <span>• 权限：通过 RBAC 映射部门与知识库权限。</span>
                <span>• 审计：记录登录、检索与文档访问事件。</span>
                <footer>复制　　继续追问　　查看引用</footer>
              </article>
            </main>
            <aside className="preview-citations">
              <h3>引用来源</h3>
              <small>点击可定位到原文</small>
              {[
                ["PDF", "身份认证接入规范", "94%"],
                ["MD", "生产环境部署手册", "91%"],
                ["DOC", "研发安全基线", "88%"],
              ].map(([type, name, score]) => (
                <article key={name}>
                  <b className={type}>{type}</b>
                  <strong>{name}</strong>
                  <span>
                    第 18 页 · <em>{score}</em>
                  </span>
                  <p>OIDC 接入应统一使用企业 IdP，并结合 MFA 与角色…</p>
                </article>
              ))}
              <div className="preview-trust">
                <ShieldCheck />
                <span>
                  回答依据 <b>5</b> 个知识片段
                  <br />
                  <small>
                    可信度　<strong>高</strong>
                  </small>
                </span>
              </div>
            </aside>
          </div>
        </div>
      </section>
    </div>
  );
}
function Landing({ onEnter, onLogin }) {
  const heroRef = useRef(null);
  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add(
      {
        reduceMotion: "(prefers-reduced-motion: reduce)",
        desktop: "(min-width: 761px)",
      },
      ({ conditions }) => {
        if (conditions.reduceMotion) return;
        gsap.from(".hero-nav", { y: -16, autoAlpha: 0, duration: .7, ease: "power3.out" });
        gsap.from(".hero-label, .hero-center h1, .hero-description, .hero-actions", {
          y: 18, autoAlpha: 0, duration: .75, stagger: .08, delay: .12, ease: "power3.out",
        });
        gsap.from(".hero-product", { y: 24, scale: .985, autoAlpha: 0, duration: .85, delay: .34, ease: "power3.out" });
        if (conditions.desktop) {
          gsap.from(".hero-float-card", {
            y: 16, autoAlpha: 0, duration: .65, stagger: { amount: .55, from: "random" }, delay: .45, ease: "power3.out",
            clearProps: "transform,opacity,visibility",
          });
        }
        gsap.from(".hero-trust-bar", { y: 14, autoAlpha: 0, duration: .7, delay: .62, ease: "power3.out" });
      },
    );
    return () => media.revert();
  }, { scope: heroRef });
  const sources = [
    [FileText, "PDF", "文档", "5.2s", "-0.5s"],
    [BookOpen, "Wiki", "知识库", "6.4s", "-1.3s"],
    [GitBranch, "Git", "代码仓库", "5.8s", "-2.1s"],
    [Braces, "API", "接口服务", "7s", "-3.2s"],
    [Database, "Database", "数据库", "6.2s", "-1.8s"],
  ];
  const capabilities = [
    [Search, "混合检索", "向量 + 关键词", "5.6s", "-0.8s"],
    [Link2, "引用溯源", "出处可查看", "6.8s", "-2.4s"],
    [ShieldCheck, "RBAC", "权限控制", "5.1s", "-1.5s"],
    [ClipboardCheck, "审计", "全链路审计", "6.1s", "-3.1s"],
    [Boxes, "Milvus / MinIO", "向量与对象存储", "7s", "-2s"],
  ];
  const trustItems = [
    [Database, "私有化部署", "数据不出域"],
    [Users, "细粒度权限", "按需访问"],
    [ShieldCheck, "全链路溯源", "来源透明"],
    [Search, "混合检索", "更准更全面"],
    [Clipboard, "审计日志", "合规可追溯"],
  ];
  const FloatingCard = ({ item, index }) => {
    const [Icon, title, copy, duration, delay] = item;
    return (
      <article
        className={`hero-float-card float-${index + 1}`}
        style={{ "--float-duration": duration, "--float-delay": delay }}
      >
        <span><Icon /></span>
        <div><b>{title}</b><small>{copy}</small></div>
      </article>
    );
  };
  return (
    <div className="landing hero-landing" ref={heroRef}>
      <header className="hero-nav">
        <Logo />
        <nav aria-label="主要导航">
          <a>产品⌄</a><a>解决方案⌄</a><a>知识库</a><a>安全与权限</a><a>开发者⌄</a>
        </nav>
        <div>
          <button className="hero-login" onClick={onLogin}>登录</button>
          <button className="hero-enter" onClick={onEnter}>进入 DevMind AI</button>
        </div>
      </header>

      <main className="hero-stage">
        <div className="hero-orbits" aria-hidden="true"><i /><i /><i /><b /><b /><b /><b /></div>
        <aside className="hero-card-stack hero-sources" aria-label="企业知识来源">
          {sources.map((item, index) => <FloatingCard item={item} index={index} key={item[1]} />)}
        </aside>
        <aside className="hero-card-stack hero-capabilities" aria-label="DevMind AI 能力">
          {capabilities.map((item, index) => <FloatingCard item={item} index={index + 5} key={item[1]} />)}
        </aside>

        <section className="hero-center">
          <p className="hero-label">Enterprise AI Knowledge Workspace</p>
          <h1>让企业知识真正可检索<br /><em>可追溯、可回答</em></h1>
          <p className="hero-description">连接内部文档、Wiki、代码仓库、API 与数据库，构建可信的企业级 AI 知识工作空间，权限可控，来源可追溯。</p>
          <div className="hero-actions">
            <button className="hero-enter" onClick={onEnter}>进入 DevMind AI <ArrowRight /></button>
            <button className="hero-demo"><span>▶</span>观看演示</button>
          </div>

          <div className="hero-product" aria-label="可信回答演示">
            <article className="hero-answer-card">
              <div className="hero-question"><MessageCircle />请问我们公司的差旅报销标准是什么？</div>
              <div className="hero-answer-copy">
                <span className="hero-ai-mark"><Logo compact /></span>
                <div>
                  <p>根据公司《差旅费用报销管理规定（2024版）》，国内差旅住宿标准如下：</p>
                  <ul><li>一线城市：不超过 800 元/晚</li><li>二线城市：不超过 600 元/晚</li><li>其他城市：不超过 400 元/晚</li></ul>
                  <p>如有特殊情况，请在申请中说明事由并提交审批。</p>
                </div>
              </div>
              <footer><span><ShieldCheck />基于企业知识库的可信回答</span><span><i />已连接企业知识库</span></footer>
            </article>
            <article className="hero-citation-card">
              <header><b>引用来源</b><span>3</span></header>
              {[
                ["PDF", "差旅费用报销管理规定（2024版）.pdf", "制度文档 · 第 4.2 条"],
                ["W", "差旅报销常见问题 FAQ", "Wiki · 更新于 2024-04-12"],
                ["X", "各城市住宿标准参考表", "表格数据 · 更新于 2024-03-18"],
              ].map(([type, title, meta]) => <div className="hero-citation-row" key={title}><strong className={`type-${type.toLowerCase()}`}>{type}</strong><span><b>{title}</b><small>{meta}</small></span></div>)}
              <button>查看全部来源 <ArrowRight /></button>
            </article>
          </div>
        </section>
      </main>

      <footer className="hero-trust-bar">
        {trustItems.map(([Icon, title, copy]) => <div key={title}><Icon /><span><b>{title}</b><small>{copy}</small></span></div>)}
      </footer>
    </div>
  );
}

function Login({ onSuccess }) {
  const [show, setShow] = useState(false);
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [method, setMethod] = useState("password");
  const [mode, setMode] = useState("login");
  const [loading, setLoading] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [debugCode, setDebugCode] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (!account.trim() && (mode === "register" || method === "password")) {
      setError(mode === "register" ? "请输入注册账号" : "请输入账号");
      return;
    }
    if ((mode === "register" || method === "password") && !password) {
      setError("请输入密码");
      return;
    }
    if ((mode === "register" || method === "phone") && (!phone.trim() || !code.trim())) {
      setError("请输入手机号和验证码");
      return;
    }
    setLoading(true);
    try {
      const data = mode === "register"
        ? await authApi.register({
            username: account.trim(),
            nickname: account.trim(),
            password,
            phone: phone.trim(),
            sms_code: code.trim(),
          })
        : method === "password"
          ? await authApi.login(account.trim(), password)
          : await authApi.smsLogin(phone.trim(), code.trim());
      setAuth(data.access_token, data.user, remember);
      onSuccess(data.user);
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setLoading(false);
    }
  };
  const isRegister = mode === "register";
  const switchMethod = () => {
    setMethod((value) => (value === "password" ? "phone" : "password"));
    setError("");
    setCodeSent(false);
  };
  const sendCode = async () => {
    if (!phone.trim()) {
      setError("请先输入手机号");
      return;
    }
    setLoading(true);
    setError("");
    setDebugCode("");
    try {
      const data = await authApi.sendCode(phone.trim());
      setCodeSent(true);
      setDebugCode(data.debug_code || "");
    } catch (sendError) {
      setError(sendError.message);
    } finally {
      setLoading(false);
    }
  };
  return (
    <main className="login-page">
      <section className="login-box">
        <div className="login-brand">
          <Logo />
          <h1>{isRegister ? "注册 DevMind AI" : "登录 DevMind AI"}</h1>
          <p>
            {isRegister
              ? "创建企业技术知识工作空间账号"
              : "访问企业内部技术知识工作空间"}
          </p>
        </div>
        <form onSubmit={submit}>
          {method === "password" || isRegister ? (
            <>
              <label>
                {isRegister ? "注册账号" : "账号 / 手机号"}
                <input
                  value={account}
                  onChange={(e) => setAccount(e.target.value)}
                  placeholder={isRegister ? "请输入注册账号" : "请输入账号或手机号"}
                  autoComplete="username"
                />
              </label>
              <label>
                密码
                <span className="password-field">
                  <input
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    type={show ? "text" : "password"}
                    placeholder="请输入密码"
                    autoComplete={
                      isRegister ? "new-password" : "current-password"
                    }
                  />
                  <button
                    type="button"
                    onClick={() => setShow((v) => !v)}
                    aria-label={show ? "隐藏密码" : "显示密码"}
                  >
                    {show ? <EyeOff /> : <Eye />}
                  </button>
                </span>
              </label>
            </>
          ) : null}
          {(method === "phone" || isRegister) && (
            <>
              <label>
                手机号
                <input
                  value={phone}
                  onChange={(e) => {
                    setPhone(e.target.value);
                    setCodeSent(false);
                  }}
                  placeholder="请输入 E.164 手机号，例如 +8613800138000"
                  inputMode="tel"
                  autoComplete="tel"
                />
              </label>
              <label>
                验证码
                <span className="code-field">
                  <input
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    placeholder="请输入验证码"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                  />
                  <button type="button" onClick={sendCode} disabled={loading}>
                    {codeSent ? "已发送" : "获取验证码"}
                  </button>
                </span>
              </label>
              {debugCode && <p className="login-code-tip">开发环境验证码：{debugCode}</p>}
            </>
          )}
          {error && <p className="login-error">{error}</p>}
          {!isRegister && (
            <div className="login-options">
              <label>
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                />
                记住登录状态
              </label>
              <button type="button">忘记密码？</button>
            </div>
          )}
          {isRegister && (
            <p className="register-note">
              注册即表示你同意 <a>用户协议</a> 与 <a>隐私政策</a>
            </p>
          )}
          <button className="login-submit" type="submit" disabled={loading}>
            {loading ? "请稍候…" : isRegister ? "注册并进入" : "登录"} {!loading && <ArrowRight />}
          </button>
        </form>
        <div className="login-switch">
          {isRegister ? "已有账号？" : "还没有账号？"}
          <button
            onClick={() => {
              setMode(isRegister ? "login" : "register");
              setMethod("password");
              setError("");
            }}
          >
            {isRegister ? "登录" : "注册账号"}
          </button>
        </div>
        {!isRegister && (
          <div className="login-sso">
            <span>其他登录方式</span>
            <button type="button" onClick={switchMethod}>
              {method === "password" ? "手机验证码登录" : "使用账号密码"}{" "}
              <ArrowRight />
            </button>
          </div>
        )}
      </section>
      <footer className="login-footer">
        © 2026 DevMind AI　 <a>隐私政策</a> · <a>用户协议</a>
      </footer>
    </main>
  );
}
function useRoute() {
  const [path, setPath] = useState(() => location.pathname);
  useEffect(() => {
    const change = () => setPath(location.pathname);
    addEventListener("popstate", change);
    return () => removeEventListener("popstate", change);
  }, []);
  return [
    path,
    (to) => {
      history.pushState({}, "", to);
      setPath(to);
    },
  ];
}
export default function App() {
  const [path, navigate] = useRoute();
  const [, refreshAuth] = useState(0);
  const loginSuccess = () => {
    refreshAuth((value) => value + 1);
    navigate("/workspace");
  };
  const logout = async () => {
    try {
      if (getToken()) await authApi.logout();
    } catch {
      // 本地令牌仍需清除，避免后端不可用时用户无法退出。
    } finally {
      clearAuth();
      navigate("/");
    }
  };
  if (path === "/login")
    return <Login onSuccess={loginSuccess} />;
  if (path === "/workspace" && !getToken())
    return <Login onSuccess={loginSuccess} />;
  if (path === "/workspace")
    return (
      <Workspace
        onLanding={() => navigate("/")}
        onLogout={logout}
      />
    );
  return (
    <Landing
      onEnter={() => navigate("/login")}
      onLogin={() => navigate("/login")}
    />
  );
}
