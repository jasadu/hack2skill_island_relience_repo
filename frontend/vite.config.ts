import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy /api to the local FastAPI (uvicorn app.main:app --port 8080). Production: Firebase Hosting rewrites /api/**.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8080" } },
});
