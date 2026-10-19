import http from 'node:http';
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 4173);
const backend = process.env.GASTRO_BACKEND_URL || 'https://90.156.213.217';
const mime = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8' };

const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  if (pathname.startsWith('/api/v1/')) {
    try {
      const target = new URL(request.url, backend);
      const headers = {};
      for (const name of ['authorization', 'x-api-key', 'idempotency-key', 'content-type', 'accept']) {
        if (request.headers[name]) headers[name] = request.headers[name];
      }
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      const upstream = await fetch(target, { method: request.method, headers, body: body.length ? body : undefined });
      response.writeHead(upstream.status, { 'Content-Type': upstream.headers.get('content-type') || 'application/json', 'Cache-Control': 'no-store' });
      response.end(Buffer.from(await upstream.arrayBuffer()));
    } catch {
      response.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
      response.end(JSON.stringify({ code: 'backend_unavailable', message: 'API временно недоступен. Попробуйте ещё раз позже.' }));
    }
    return;
  }
  const safePath = pathname === '/' ? '/index.html' : pathname;
  const fullPath = path.resolve(root, '.' + decodeURIComponent(safePath));
  if (!fullPath.startsWith(root + path.sep)) { response.writeHead(403); response.end(); return; }
  try {
    const info = await stat(fullPath);
    if (!info.isFile()) throw new Error('not a file');
    response.writeHead(200, { 'Content-Type': mime[path.extname(fullPath)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    createReadStream(fullPath).pipe(response);
  } catch { response.writeHead(404); response.end('Not found'); }
});
server.listen(port, '127.0.0.1', () => console.log(`ГастроГид: http://127.0.0.1:${port}/`));
