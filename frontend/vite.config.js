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
      '/upload-training-data': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/train-model': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/clear-model': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/generate-music': {
        target: 'http://localhost:8888',
        changeOrigin: true
      },
      '/download': {
        target: 'http://localhost:8888',
        changeOrigin: true
      }
    }
  }
})
