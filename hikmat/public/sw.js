/* Hikmat PWA service worker — offline-first app shell + content.
   Scope: /assets/hikmat/. Bump CACHE to ship an update to installed PWAs. */
const CACHE = "hikmat-pwa-v11";  // v11: nothing locks (v10: release-stamped URLs, v9: level tests)
// The release token, derived from CACHE so there is exactly ONE thing to bump. /assets/ is served
// `cache-control: immutable, max-age=31536000` — correct for Frappe's hash-named bundles, wrong
// for ours, which are plain filenames that change content under a fixed name. A proxy therefore
// answers the next release's game.html from its year-old copy (observed on prod: x-proxy-cache
// HIT serving a week-old game.html with none of the new code in it, while the origin had the new
// file). Stamping the release onto the URL gives every release its own cache key.
// www/play.py reads this same line, so the redirect and the shell always agree.
const REL = CACHE.slice(CACHE.lastIndexOf("-") + 1);
const BASE = "/assets/hikmat/";
const rel = (p) => BASE + p + "?r=" + REL;
const DOC = rel("game.html");     // this release's shell document
const SHELL = [
  DOC,
  rel("curriculum.json"),            // full 283-lesson offline baseline (survives localStorage eviction)
  rel("testbank.json"),              // the L1–L5 level-test question bank, same contract
  rel("manifest.webmanifest"),
  BASE + "icons/icon-192.png",       // icons are content-stable; no token needed
  BASE + "icons/icon-512.png",
  BASE + "icons/icon-512-maskable.png",
  BASE + "icons/icon-180.png",
];
// read APIs cached (network-first) so the game keeps full content offline even if localStorage is wiped
const CACHED_API = ["hikmat.api.get_courses", "hikmat.api.get_structure", "hikmat.api.get_settings", "hikmat.api.get_test_bank"];

self.addEventListener("install", (e) => {
  // The shell DOCUMENT is not optional. activate() deletes the previous cache outright, so a
  // half-filled install that still reaches "installed" can leave a learner with no shell at all
  // — she taps the update toast on a weak link, the 936KB document never landed, the old cache
  // is deleted anyway, and the next offline open is a browser error page inside a standalone
  // PWA with no address bar to recover from. Failing the install instead leaves the previous
  // worker in charge with its cache intact, and the browser retries on her next visit.
  // Everything else stays tolerant. Do NOT unconditionally skipWaiting — the page asks us to
  // activate (see 'message') so an update never reloads a child mid-activity.
  e.waitUntil(caches.open(CACHE).then((c) =>
    c.add(DOC).then(() => Promise.allSettled(SHELL.filter((u) => u !== DOC).map((u) => c.add(u))))
  ));
});

self.addEventListener("message", (e) => { if (e.data === "SKIP_WAITING") self.skipWaiting(); });

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function networkFirst(req, opts) {
  return fetch(req)
    .then((res) => { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); return res; })
    .catch(() => caches.match(req, opts || undefined));
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;                       // never touch POSTs (login, submit_attempt)
  const url = new URL(req.url);

  if (url.pathname.startsWith("/api/method/")) {
    // cache only the content read endpoints; other API GETs (roster etc.) go straight to the network
    if (CACHED_API.some((m) => url.pathname.indexOf(m) !== -1)) e.respondWith(networkFirst(req));
    return;
  }
  if (!url.pathname.startsWith(BASE)) return;             // only manage this app's own static files

  const isDoc = req.mode === "navigate" || url.pathname.endsWith("game.html");
  const anyRel = { ignoreSearch: true };   // inside BASE the query is only ever ?r=<release>
  if (isDoc) {
    // Always fetch THIS release's document, whatever URL the navigation actually used. The PWA's
    // start_url, every bookmark and every shared link are the bare /assets/hikmat/game.html, and
    // the proxy keeps a SEPARATE immutable entry per Accept-Encoding variant — the exact string
    // modern Chrome sends ("gzip, deflate, br, zstd") went on serving a week-old build for hours
    // after the release that was supposed to fix it, while three other variants were current.
    // Rewriting the request here leaves the address bar on start_url — so PWA identity and
    // bookmarks still work — while the bytes always come from the current release's cache key.
    e.respondWith(networkFirst(DOC, anyRel).then((r) => r || caches.match(BASE + "game.html", anyRel)));
  } else {
    e.respondWith(caches.match(req, anyRel).then((r) => r || fetch(req).then((res) => {
      const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); return res;
    })));
  }
});
