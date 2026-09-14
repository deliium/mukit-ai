import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Long LLM routes (staged generation, arrangement, development) need a long
// proxyTimeout while waiting on upstream providers. Do not set `timeout` here:
// http-proxy applies that to the *incoming* browser socket and can abort early.
const LONG_LLM_PROXY = {
  target: 'http://localhost:8888',
  changeOrigin: true,
  proxyTimeout: 300_000,
}

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
        ...LONG_LLM_PROXY,
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
        ...LONG_LLM_PROXY,
      },
      '/harmony': {
        ...LONG_LLM_PROXY,
      },
      '/composition': {
        ...LONG_LLM_PROXY,
      },
      '/ready': {
        target: 'http://localhost:8888',
        changeOrigin: true
      }
    }
  }
})
