import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      '/api': {
        target: process.env.ARGUS_FLYWHEEL_API ?? 'http://127.0.0.1:8743',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
