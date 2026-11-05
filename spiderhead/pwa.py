"""Make the static dashboard installable on iPhone / Android ("Add to Home Screen").

* ``manifest.webmanifest`` — name, icons, standalone display, theme colours.
* ``sw.js`` — service worker: pages and data are network-first with an offline
  copy; assets are cache-first. The cache name changes on every build so an
  update replaces the old copy.
* Icons are shipped in ``spiderhead/static`` (regenerate: scripts/make_icons.py).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
ICONS = ["icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "favicon-32.png"]

SW = """// Smartmoney Radar service worker — generated, do not edit.
const CACHE = "smr-__VERSION__";
const CORE = __CORE__;
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
function remember(req, res) {
  if (res && res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
  return res;
}
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const asset = /\\/(assets|img)\\//.test(url.pathname);
  if (asset) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => remember(req, res))));
    return;
  }
  e.respondWith(fetch(req).then((res) => remember(req, res)).catch(() =>
    caches.match(req).then((hit) => hit || caches.match("./index.html"))));
});
"""

REGISTER_JS = """
(function(){
const me=document.currentScript&&document.currentScript.src||'';
const root=me.replace(/assets\\/app\\.js.*$/,'');
if('serviceWorker' in navigator && /^https?:/.test(location.protocol)){
  navigator.serviceWorker.register(root+'sw.js',{scope:root}).catch(()=>{});
}
const bar=document.getElementById('sh-offline');
function upd(){ if(bar) bar.style.display=navigator.onLine?'none':'block'; }
window.addEventListener('online',upd);window.addEventListener('offline',upd);upd();
})();
"""


def write_pwa(out: Path, core_pages: list[str], version: str | None = None) -> None:
    out = Path(out)
    icons = out / "assets" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    for name in ICONS:
        src = STATIC / name
        if src.exists():
            shutil.copyfile(src, icons / name)
    manifest = {
        "name": "Smartmoney Radar 大神雷達",
        "short_name": "大神雷達",
        "description": "追蹤投資大神、投行與總經：說了什麼、實際做了什麼、準不準",
        "lang": "zh-Hant",
        "start_url": "./index.html",
        "scope": "./",
        "display": "standalone",
        "background_color": "#f9f9f7",
        "theme_color": "#2a78d6",
        "icons": [
            {"src": "assets/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "assets/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "assets/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    (out / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    core = ["./", *[f"./{p}" for p in core_pages], "./assets/style.css", "./assets/app.js",
            "./manifest.webmanifest", "./assets/icons/icon-192.png"]
    version = version or datetime.now().strftime("%Y%m%d%H%M%S")
    (out / "sw.js").write_text(SW.replace("__VERSION__", version).replace("__CORE__", json.dumps(core)),
                               encoding="utf-8")
