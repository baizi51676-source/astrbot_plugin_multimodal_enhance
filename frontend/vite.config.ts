import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

/**
 * 多模态理解增强 · 插件控制台前端。
 *
 * 构建产物直接输出到 AstrBot 插件目录 pages/console：
 * - `base: './'` 是硬要求，页面在 iframe 里以子路径访问，绝对路径会 404；
 * - 关闭 sourcemap，产物随插件打包分发。
 */
export default defineConfig({
  base: './',
  plugins: [
    react(),
    tailwindcss({
      optimize: true,
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: '../pages/console',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
    sourcemap: false,
  },
});
