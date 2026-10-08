// 前端常量

// 默认留空：开发环境走 vite.config.js 的 /api 代理到后端 15200
// 如需直连后端，可在 front/.env 中配置 VITE_API_BASE_URL
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

// 团队过滤项
export const SOURCE_FILTERS = ["infra", "backend", "frontend", "data", "ops"];
