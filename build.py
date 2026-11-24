#!/usr/bin/env python3
"""Build NewsHub as a static site (for GitHub Pages).

Fetches every feed and homepage once, looks up share images for stories that
came without one, and writes:

    site/index.html   the app
    site/news.json    all the stories

GitHub Actions runs this on a schedule (see .github/workflows/update.yml).
Run it locally with:  python3 build.py
"""
import json
import os
import urllib.request
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import server

ROOT = Path(__file__).parent
OUT = ROOT / "site"
MAX_THUMB_LOOKUPS = 400


def previous_images():
    """Reuse images found by the last published build, so each run only looks up new stories."""
    base = os.environ.get("PAGES_URL", "").rstrip("/")
    if not base:
        return {}
    try:
        req = urllib.request.Request(f"{base}/news.json?t={int(time.time())}", headers={"User-Agent": server.USER_AGENT})
        with urllib.request.urlopen(req, timeout=15, context=server.SSL_CTX) as r:
            old = json.loads(r.read())
        images = {a["link"]: a["image"] for a in old.get("articles", []) if a.get("image")}
        images.update({link: None for link in old.get("no_image", [])})  # looked up before, nothing found
        return images
    except Exception as e:
        print(f"No previous build to reuse images from ({e})")
        return {}


def main():
    t = time.time()
    data = server.load_all()
    print(f"Loaded {len(data['articles'])} stories in {time.time() - t:.1f}s")

    # Pages can't look up images on demand, so do it now for stories that lack one
    known = previous_images()
    for a in data["articles"]:
        if not a["image"] and known.get(a["link"]):
            a["image"] = known[a["link"]]
    print(f"Reused {sum(1 for v in known.values() if v)} images from the last build")
    missing = [a for a in data["articles"]
               if not a["image"] and a["link"] not in known and "news.google.com" not in a["link"]]
    missing.sort(key=lambda a: (a.get("rank") is None, a.get("rank") or 0))  # front-page stories first
    missing = missing[:MAX_THUMB_LOOKUPS]
    t = time.time()
    with ThreadPoolExecutor(max_workers=32) as pool:
        for a, img in zip(missing, pool.map(lambda a: server.thumbnail(a["link"]), missing)):
            a["image"] = img
    found = sum(1 for a in missing if a["image"])
    print(f"Found {found}/{len(missing)} missing images in {time.time() - t:.1f}s")

    data["static"] = True
    data["no_image"] = sorted({a["link"] for a in data["articles"] if not a["image"]} &
                              ({a["link"] for a in missing} | {k for k, v in known.items() if v is None}))
    for s in data["sources"]:
        print(f"  {s['name']:20} {s.get('frontpage', 'feed'):5} feeds ok={s.get('ok', 0)} failed={s.get('failed', 0)}")

    OUT.mkdir(exist_ok=True)
    shutil.copy(ROOT / "static" / "index.html", OUT / "index.html")
    (OUT / "news.json").write_text(json.dumps(data, separators=(",", ":")))
    (OUT / ".nojekyll").write_text("")
    print(f"Wrote {OUT}/ ({(OUT / 'news.json').stat().st_size // 1024} KB of news)")

    if not data["articles"]:
        raise SystemExit("No stories loaded - not publishing an empty site")


if __name__ == "__main__":
    main()
