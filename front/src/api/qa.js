// 后端 API 封装：所有请求都走这里，组件里不直接写 fetch。
import { API_BASE_URL } from "../utils/constants";

const TOKEN_KEY = "devmind_token";
const USER_KEY = "devmind_user";

// ---------- 登录状态存储 ----------
export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY);
}

export function setAuth(token, user, remember = true) {
  clearAuth();
  const storage = remember ? localStorage : sessionStorage;
  storage.setItem(TOKEN_KEY, token);
  storage.setItem(USER_KEY, JSON.stringify(user));
}

export function getStoredUser() {
  const raw = localStorage.getItem(USER_KEY) || sessionStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
}

// ---------- 通用请求 ----------
async function request(path, { method = "GET", body, auth = false } = {}) {
  const isForm = body instanceof FormData;
  const headers = isForm ? {} : { "Content-Type": "application/json" };
  if (auth) {
    const token = getToken();
    if (!token) throw new Error("请先登录");
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
  });

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = data?.error?.message || data?.detail || `请求失败（${response.status}）`;
    const error = new Error(message);
    error.status = response.status;
    error.code = data?.error?.code;
    throw error;
  }
  return data?.data ?? data;
}

// ---------- 认证接口 ----------
export const authApi = {
  sendCode: (phone) => request("/api/v1/auth/send-code", { method: "POST", body: { phone } }),
  register: (payload) => request("/api/v1/auth/register", { method: "POST", body: payload }),
  login: (account, password) => request("/api/v1/auth/login", { method: "POST", body: { account, password } }),
  smsLogin: (phone, sms_code) => request("/api/v1/auth/login/sms", { method: "POST", body: { phone, sms_code } }),
  logout: () => request("/api/v1/auth/logout", { method: "POST", auth: true }),
  me: () => request("/api/v1/me", { auth: true }),
};

// ---------- 当前用户资料 ----------
export const profileApi = {
  get: () => request("/api/v1/me/profile", { auth: true }),
  update: (payload) => request("/api/v1/me/profile", { method: "PATCH", body: payload, auth: true }),
};

export const preferencesApi = {
  get: () => request("/api/v1/me/preferences", { auth: true }),
  update: (payload) =>
    request("/api/v1/me/preferences", { method: "PATCH", body: payload, auth: true }),
};

export const organizationApi = {
  users: (params = {}) => {
    const query = new URLSearchParams(params);
    return request(`/api/v1/organization/users?${query}`, { auth: true });
  },
};

// ---------- 正式 RAG 智能问答 ----------
export const chatApi = {
  createSession: (payload = {}) =>
    request("/api/v1/sessions", { method: "POST", body: payload, auth: true }),
  listSessions: (params = {}) => {
    const query = new URLSearchParams(params);
    return request(`/api/v1/sessions?${query}`, { auth: true });
  },
  getSession: (id) => request(`/api/v1/sessions/${id}`, { auth: true }),
  updateSession: (id, title) =>
    request(`/api/v1/sessions/${id}`, { method: "PATCH", body: { title }, auth: true }),
  deleteSession: (id) =>
    request(`/api/v1/sessions/${id}`, { method: "DELETE", auth: true }),
  createMessage: (payload) =>
    request("/api/v1/messages", { method: "POST", body: payload, auth: true }),
  getMessage: (id) => request(`/api/v1/messages/${id}`, { auth: true }),
  getCitations: (id) =>
    request(`/api/v1/messages/${id}/citations`, { auth: true }),
  getCitation: (id) => request(`/api/v1/citations/${id}`, { auth: true }),
  listFavorites: (params = {}) => {
    const query = new URLSearchParams(params);
    return request(`/api/v1/favorites?${query}`, { auth: true });
  },
  favorite: (messageId) =>
    request(`/api/v1/favorites/messages/${messageId}`, { method: "PUT", auth: true }),
  unfavorite: (messageId) =>
    request(`/api/v1/favorites/messages/${messageId}`, { method: "DELETE", auth: true }),
  testRetrieval: (payload) =>
    request("/api/v1/retrieval/test", { method: "POST", body: payload, auth: true }),
  connectStream: (streamUrl, onEvent) => {
    const origin = API_BASE_URL || window.location.origin;
    const socket = new WebSocket(`${origin.replace(/^http/, "ws")}${streamUrl}`);
    socket.onmessage = ({ data }) => onEvent(JSON.parse(data));
    return socket;
  },
};

export const knowledgeBaseApi = {
  list: (params = {}) => {
    const query = new URLSearchParams(params);
    return request(`/api/v1/knowledge-bases?${query}`, { auth: true });
  },
  detail: (id) => request(`/api/v1/knowledge-bases/${id}`, { auth: true }),
  create: (payload) =>
    request("/api/v1/knowledge-bases", { method: "POST", body: payload, auth: true }),
  update: (id, payload) =>
    request(`/api/v1/knowledge-bases/${id}`, { method: "PATCH", body: payload, auth: true }),
  remove: (id) =>
    request(`/api/v1/knowledge-bases/${id}`, { method: "DELETE", auth: true }),
  members: (id) => request(`/api/v1/knowledge-bases/${id}/members`, { auth: true }),
  setMember: (id, userId, role) =>
    request(`/api/v1/knowledge-bases/${id}/members/${userId}`, {
      method: "PUT",
      body: { role },
      auth: true,
    }),
  removeMember: (id, userId) =>
    request(`/api/v1/knowledge-bases/${id}/members/${userId}`, {
      method: "DELETE",
      auth: true,
    }),
};

export const documentApi = {
  list: (params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") query.set(key, value);
    });
    return request(`/api/v1/documents?${query}`, { auth: true });
  },
  detail: (id) => request(`/api/v1/documents/${id}`, { auth: true }),
  upload: ({ file, knowledgeBaseId, displayName = "", metadata = {} }) => {
    const body = new FormData();
    body.append("file", file);
    body.append("knowledge_base_id", knowledgeBaseId);
    if (displayName) body.append("display_name", displayName);
    body.append("metadata", JSON.stringify(metadata));
    return request("/api/v1/documents", { method: "POST", body, auth: true });
  },
  update: (id, payload) =>
    request(`/api/v1/documents/${id}`, { method: "PATCH", body: payload, auth: true }),
  remove: (id) => request(`/api/v1/documents/${id}`, { method: "DELETE", auth: true }),
  reindex: (id, force = false) =>
    request(`/api/v1/documents/${id}/reindex`, {
      method: "POST",
      body: { force },
      auth: true,
    }),
  chunks: (id, params = {}) => {
    const query = new URLSearchParams(params);
    return request(`/api/v1/documents/${id}/chunks?${query}`, { auth: true });
  },
  task: (id) => request(`/api/v1/documents/${id}/task`, { auth: true }),
  retryTask: (id) =>
    request(`/api/v1/documents/${id}/task/retry`, { method: "POST", auth: true }),
};

// ---------- 知识库接口 ----------
export const knowledgeApi = {
  list: (params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") query.set(key, value);
    });
    return request(`/api/v1/knowledge/docs?${query.toString()}`);
  },
  detail: (id) => request(`/api/v1/knowledge/docs/${id}`),
};

// ---------- FAQ 接口 ----------
export const faqApi = {
  list: (params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") query.set(key, value);
    });
    return request(`/api/v1/faq?${query.toString()}`);
  },
  detail: (id) => request(`/api/v1/faq/${id}`),
  search: (question) => request("/api/v1/faq/search", { method: "POST", body: { question }, auth: true }),
};

// ---------- 客户端启动配置 ----------
export const bootstrapApi = {
  get: () => request("/api/v1/client/bootstrap"),
};
