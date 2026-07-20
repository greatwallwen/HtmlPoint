import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    target: "es2022",
    sourcemap: false,
    assetsDir: "assets",
    manifest: true,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          validation: ["zod"],
        },
      },
    },
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
