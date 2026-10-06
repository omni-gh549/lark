import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte()],
  // lightningcss keeps only the -webkit- backdrop-filter, which Chromium ignores
  css: { lightningcss: { targets: { chrome: 114 << 16, firefox: 125 << 16, safari: (16 << 16) | (4 << 8) } } },
  build: { cssMinify: "lightningcss" },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
