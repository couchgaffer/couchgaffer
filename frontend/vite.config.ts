import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // Local dev only: forward API and WebSocket calls to the backend. WATCHPARTY_BACKEND moves it off port 8000,
  // for a second checkout or a smoke run beside a running server.
  const backend = loadEnv(mode, ".", "WATCHPARTY_").WATCHPARTY_BACKEND || "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": backend,
        "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
        "/health": backend,
      },
    },
  };
});
