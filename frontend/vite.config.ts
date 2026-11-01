/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/query": "http://localhost:8080" } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/setupTests.ts"] },
});
