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
      },
      '/export': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/projects': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/imports': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/analysis': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/motifs': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/ready': {
        target: 'http://localhost:8888',
        changeOrigin: true
      }
    }
  }
})
