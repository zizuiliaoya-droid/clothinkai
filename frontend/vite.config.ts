import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const srcUrl = new URL("./src", import.meta.url);
const decodedSrcPath = decodeURIComponent(srcUrl.pathname);
const srcPath = srcUrl.hostname
  ? `//${srcUrl.hostname}${decodedSrcPath}`
  : decodedSrcPath.replace(/^\/([A-Za-z]:\/)/, "$1");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": srcPath,
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Docker on Windows/WSL bind mount 不传递 inotify 事件，开启轮询保证 HMR
    watch: {
      usePolling: true,
      interval: 300,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    chunkSizeWarningLimit: 1000,
  },
});
