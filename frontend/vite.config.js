import { defineConfig } from 'vite';

const apiTarget = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000';

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, timeout: 0, proxyTimeout: 0 },
      '/health': apiTarget,
    },
  },
  test: { environment: 'jsdom', clearMocks: true },
});
