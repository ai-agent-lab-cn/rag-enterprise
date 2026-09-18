// 临时配置：本地运行检查用，代理指向 8001。
// 8000 被一个跑在废纸篓副本上的僵尸 uvicorn 占着，不动它。检查结束后删除本文件。
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    proxy: { "/api": "http://127.0.0.1:8001" },
  },
});
