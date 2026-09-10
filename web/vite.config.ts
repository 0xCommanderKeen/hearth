import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const target =
    loadEnv(mode, ".", "HEARTH_").HEARTH_API_PROXY || "http://127.0.0.1:8766";
  return {
    // Let jsdom own browser storage in every worker, including on Node 26.
    test: { execArgv: ["--no-experimental-webstorage"] },
    plugins: [react()],
    server: {
      proxy: {
        "/api": target,
        "/health": target,
      },
    },
  };
});
