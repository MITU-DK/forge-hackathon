import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: UI on :5173 proxies /api to the FastAPI server on :8000 (no CORS pain).
// host: true lets a phone on the same Wi-Fi open http://<laptop-ip>:5173 for real-device testing.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true, proxy: { "/api": "http://localhost:8000" } },
});
