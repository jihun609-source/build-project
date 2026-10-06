import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// 개발: npm run dev (5173) → API는 FastAPI(8000)로 프록시
// 배포: npm run build → web/dist 를 FastAPI가 /ui 로 서빙
const API = `http://127.0.0.1:${process.env.AUTOSET_PORT || 8000}`
const paths = ['/feed', '/items', '/config', '/presets', '/edit', '/prompts', '/upload', '/system', '/auth',
  '/files', '/schedule', '/health']

export default defineConfig({
  base: '/ui/',
  plugins: [vue(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      ...Object.fromEntries(paths.map(p => [p, { target: API, changeOrigin: false }])),
      '/ws': { target: API.replace('http', 'ws'), ws: true },
    },
  },
  // 열려 있는 화면이 이전 빌드의 메뉴 파일을 계속 불러올 수 있도록 보존한다.
  build: { outDir: 'dist', emptyOutDir: false, chunkSizeWarningLimit: 1500 },
})
