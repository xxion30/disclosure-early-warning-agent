import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// /api/* 요청은 FastAPI(8000)로 넘긴다. CORS 걱정 없이 SSE도 같은 경로로 받는다.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
