/* fileweb service worker —— 满足 PWA 安装要求 + 基础离线缓存 */
const CACHE = "fileweb-v1";
const CORE = ["/", "/manifest.json", "/icon-192.png", "/icon-512.png",
              "/apple-touch-icon.png", "/favicon-32.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // 导航页 / API 一律网络优先，保证文件内容最新
  if (req.mode === "navigate" || url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }
  // 静态资源：缓存优先，后台更新
  e.respondWith(
    caches.match(req).then((hit) =>
      hit || fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
    )
  );
});
