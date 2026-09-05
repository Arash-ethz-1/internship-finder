import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API port lives in one place and both halves of `dev.py` read it. It is
// not 8000 because on at least one machine something unkillable holds that
// port: uvicorn then fails to bind, its child dies, and the browser keeps
// talking to whatever answered there first -- which looks exactly like the
// backend ignoring every change you make.
const API_PORT = process.env.API_PORT ?? "8010";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Keeps every request same-origin in dev, so api/client.ts can use
    // relative paths and CORS never bites during development.
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: true,
      },
    },
  },
});
