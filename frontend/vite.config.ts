import react from '@vitejs/plugin-react'
// vitest/config は vite の defineConfig に `test` フィールドの型を足したもの。
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // 既定の `assets/` は SPA のビューア `/assets/:id` と URL が衝突するので変える。
    // backend/app/main.py の静的配信(/static)と合わせること。
    assetsDir: 'static',
  },
  server: {
    proxy: {
      // backend(FastAPI)は 127.0.0.1:8000 で待ち受ける想定。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
