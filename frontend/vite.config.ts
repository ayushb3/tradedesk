import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Bind 0.0.0.0 + accept any Host so a Cloudflare tunnel (or LAN device)
    // can reach the dev server. Dev-only; production is `vite build` + static.
    host: true,
    allowedHosts: true,
    // Over a tunnel the page loads at https://<sub>.trycloudflare.com but Vite's
    // HMR client otherwise dials ws://localhost:5173 and fails. When TUNNEL_HOST
    // is set, point HMR at the public wss host on 443. Unset => normal local dev.
    hmr: process.env.TUNNEL_HOST
      ? { host: process.env.TUNNEL_HOST, protocol: "wss", clientPort: 443 }
      : undefined,
    // Proxy the frozen backend API contract. `/api/*` -> FastAPI on :9000.
    proxy: {
      "/api": {
        target: "http://localhost:9000",
        changeOrigin: true,
      },
    },
  },
});
