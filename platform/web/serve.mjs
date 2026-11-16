// Serves the exported web build (`dist/`, made by `npm run build:web`) on one port.
// Plain Node, no dependencies. Unknown paths fall back to index.html so app routes such as
// /home or /conversation/123 load the app, which then routes on the client.
//
//   node serve.mjs            -> http://localhost:3000
//   PORT=4000 node serve.mjs
//
// Requests under /api/ are forwarded to the ASK server (API_ORIGIN, default http://localhost:8000), so the
// browser sees one origin and the session cookie stays first-party.

import { createReadStream, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("./dist", import.meta.url)));
const port = Number(process.env.PORT ?? 3000);
const host = process.env.HOST ?? "0.0.0.0";
const apiOrigin = new URL(process.env.API_ORIGIN ?? "http://localhost:8000");

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".onnx": "application/octet-stream",
  ".txt": "text/plain; charset=utf-8",
};

function fileFor(urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath.split("?")[0]);
  } catch {
    return null;
  }
  const candidate = resolve(join(root, normalize(decoded)));
  // Never serve anything outside dist/.
  if (candidate !== root && !candidate.startsWith(root + sep)) return null;
  for (const path of [candidate, join(candidate, "index.html"), `${candidate}.html`]) {
    try {
      if (statSync(path).isFile()) return path;
    } catch {
      // try the next candidate
    }
  }
  return null;
}

function proxyToApi(req, res) {
  const send = apiOrigin.protocol === "https:" ? httpsRequest : httpRequest;
  const headers = { ...req.headers, host: apiOrigin.host };
  const client = req.socket.remoteAddress ?? "";
  headers["x-forwarded-for"] = headers["x-forwarded-for"] ? `${headers["x-forwarded-for"]}, ${client}` : client;
  const upstream = send(
    {
      protocol: apiOrigin.protocol,
      hostname: apiOrigin.hostname,
      port: apiOrigin.port || (apiOrigin.protocol === "https:" ? 443 : 80),
      method: req.method,
      path: req.url,
      headers,
    },
    (answer) => {
      res.writeHead(answer.statusCode ?? 502, answer.headers);
      answer.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (res.headersSent) {
      res.destroy();
      return;
    }
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: { code: "api_unreachable", message: "The ASK server is not reachable." } }));
  });
  req.pipe(upstream);
}

createServer((req, res) => {
  if ((req.url ?? "").startsWith("/api/")) {
    proxyToApi(req, res);
    return;
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { Allow: "GET, HEAD" }).end();
    return;
  }
  const found = fileFor(req.url ?? "/");
  const path = found ?? join(root, "index.html");
  const isAsset = found !== null && found.includes(`${sep}_expo${sep}`);
  res.writeHead(200, {
    "Content-Type": types[extname(path).toLowerCase()] ?? "application/octet-stream",
    "X-Content-Type-Options": "nosniff",
    // Hashed bundles never change; the HTML must always be fresh.
    "Cache-Control": isAsset ? "public, max-age=31536000, immutable" : "no-cache",
  });
  if (req.method === "HEAD") {
    res.end();
    return;
  }
  createReadStream(path)
    .on("error", () => res.destroy())
    .pipe(res);
}).listen(port, host, () => {
  console.log(`ASK web app: http://localhost:${port}`);
});
