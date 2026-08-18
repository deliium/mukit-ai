import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/health': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/llm': {
        target: 'http://localhost:8888',
        changeOrigin: true
      }
    }
  }
})
