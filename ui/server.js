/* Express UI: serves Bootstrap pages, proxies /api/* to FastAPI :8000. No business logic. */
const express = require('express');
const path = require('path');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();
const API_TARGET = process.env.API_TARGET || 'http://127.0.0.1:8000';
const PORT = process.env.UI_PORT || 3000;

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));
app.use(express.static(path.join(__dirname, 'public')));

// Mount the proxy BEFORE any body parser. A JSON body parser would consume the
// request stream, leaving the upstream waiting on Content-Length forever (the
// "Start research" request then hangs with no response).
app.use('/api', createProxyMiddleware({
  target: API_TARGET,
  changeOrigin: true,
  pathRewrite: { '^/api': '' },
  onProxyReq: (proxyReq) => { proxyReq.setHeader('X-Accel-Buffering', 'no'); },
  onError: (err, req, res) => {
    console.error(`proxy error ${req.method} ${req.originalUrl}: ${err.message}`);
    if (!res.headersSent) res.status(502).json({ error: 'api unavailable', detail: err.message });
  },
}));

app.get('/', (_req, res) => res.render('index'));
app.get('/health', (_req, res) => res.json({ ok: true }));

app.listen(PORT, '127.0.0.1', () => {
  console.log(`UI on http://127.0.0.1:${PORT} -> API ${API_TARGET}`);
});
