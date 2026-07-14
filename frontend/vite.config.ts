import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В dev-режиме проксируем /api и /media на бэкенд FastAPI (localhost:8000),
// чтобы фронтенд использовал относительные пути.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/media": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
