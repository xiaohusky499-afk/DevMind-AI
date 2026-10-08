import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端开发服务器配置
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 开发时也可把 /api 代理到后端，避免跨域问题
      "/api": {
        target: "http://127.0.0.1:15200",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
