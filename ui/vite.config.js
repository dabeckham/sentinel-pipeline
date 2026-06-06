import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': { target: 'http://orchestrator:8000', changeOrigin: true },
      '/ws': { target: 'ws://orchestrator:8000', ws: true, changeOrigin: true },
    },
  },
})
