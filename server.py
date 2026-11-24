#!/usr/bin/env python3
"""NewsHub - an all-in-one news reader.

Pulls RSS/Atom feeds from the sources in feeds.json, reads each outlet's
homepage so stories keep the same order and headlines as the real site,
merges everything, and serves a web UI at http://localhost:8777. Standard library only - no installs needed.

    python3 server.py            # then open http://localhost:8777
"""
import html
import json
import os
import re
import ssl
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import frontpage

PORT = int(os.environ.get("PORT", 8777))
ROOT = Path(__file__).parent
CACHE_SECONDS = 3 * 60          # how often to re-check every site
MAX_AGE = 3 * 24 * 3600         # drop stories older than this (unless still on a front page)
FRONTPAGE_MAX_AGE = 7 * 24 * 3600  # ...but never show front-page items older than a week
USER_AGENT = "Mozilla/5.0 (Macintosh) NewsHub/1.0"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130 Safari/537.36")

def _ssl_context():
    """python.org builds on macOS often ship without CA certs; fall back to the system bundle."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and Path("/etc/ssl/cert.pem").exists():
        ctx.load_verify_locations("/etc/ssl/cert.pem")
    return ctx


SSL_CTX = _ssl_context()
_cache = {"time": 0, "data": None}
_lock = threading.Lock()


def local(tag):
    """Strip the XML namespace from a tag name."""
    return tag.rsplit("}", 1)[-1].lower()


def child(el, *names):
    for c in el:
        if local(c.tag) in names:
            return c
    return None


def text(el, *names):
    c = child(el, *names)
    return (c.text or "").strip() if c is not None else ""


def clean(raw):
    raw = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def find_image(item, desc_html):
    best, best_w = None, -1
    for c in item.iter():
        name = local(c.tag)
        if name in ("content", "thumbnail") and c.get("url"):
            if c.get("medium") not in (None, "image") and name == "content":
                continue
            w = int(c.get("width") or 0)
            if w > best_w:
                best, best_w = c.get("url"), w
        elif name == "enclosure" and (c.get("type") or "").startswith("image") and not best:
            best = c.get("url")
    if not best and desc_html:
        m = re.search(r'<img[^>]+src="([^"]+)"', desc_html)
        if m:
            best = m.group(1)
    if best and "ichef.bbci.co.uk" in best:  # BBC ships 240px thumbs; ask for a sharper size
        best = best.replace("/standard/240/", "/standard/800/").replace("/240x135/", "/800x450/")
    return best


def fetch(source, feed):
    req = urllib.request.Request(feed["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
        root = ET.fromstring(r.read())

    items = [e for e in root.iter() if local(e.tag) in ("item", "entry")]
    out = []
    for it in items[:40]:
        title = clean(text(it, "title"))
        link = text(it, "link")
        if not link:
            l = child(it, "link")
            link = l.get("href", "") if l is not None else ""
        if not title or not link:
            continue
        desc_html = text(it, "description", "summary", "encoded", "content")
        summary = clean(desc_html)
        if source.get("via_google") or feed.get("via_google"):
            # Google News titles look like "Headline - CNN"; its summaries are just link lists
            title = re.sub(r"\s+-\s+[^-]+$", "", title)
            summary = ""
        out.append({
            "title": title,
            "link": link,
            "summary": summary[:400],
            "image": find_image(it, desc_html),
            "published": parse_date(text(it, "pubdate", "published", "updated", "date")),
            "source": source["id"],
            "categories": [feed["category"]],
        })
    return out


def canon(url):
    """Normalise an article URL so the same story matches across homepage and RSS."""
    u = urlparse(url)
    key = u.netloc.replace("www.", "").replace("abcnews.go.com", "abcnews.com") + u.path.rstrip("/")
    if "id=" in u.query:
        key += "?" + "&".join(q for q in u.query.split("&") if q.startswith("id="))
    return key.lower()


def date_from_url(url):
    m = re.search(r"/(20\d\d)/(\d\d|[a-z]{3})/(\d\d)/", url)
    if not m:
        return None
    y, mo, d = m.groups()
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    mo = months.index(mo) + 1 if mo in months else int(mo)
    try:
        return datetime(int(y), mo, int(d), 12, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def fetch_homepage(source):
    req = urllib.request.Request(source["homepage"], headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en"})
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
        page = r.read().decode("utf-8", "replace")
    items = frontpage.extract(page, source["homepage"])
    if len(items) < 5:
        raise ValueError(f"only found {len(items)} stories")
    return items


_last_good = {}


def resilient(fn, key, *args):
    """Retry once on a network hiccup; if it still fails, reuse the last copy that worked."""
    for attempt in (1, 2):
        try:
            result = fn(*args)
            _last_good[key] = result
            return result
        except Exception:
            if attempt == 2:
                if key in _last_good:
                    return _last_good[key]
                raise
            time.sleep(1)


def load_all():
    config = json.loads((ROOT / "feeds.json").read_text())
    jobs = [(s, f) for s in config["sources"] for f in s["feeds"]]
    articles, status, feed_lists = {}, {}, {}
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [(s, f, pool.submit(resilient, fetch, f["url"], s, f)) for s, f in jobs]
        home_futures = {s["id"]: pool.submit(resilient, fetch_homepage, s["homepage"], s) for s in config["sources"] if s.get("homepage")}
        for s, f, fut in futures:
            st = status.setdefault(s["id"], {"ok": 0, "failed": 0})
            try:
                items = fut.result()
                feed_lists.setdefault(s["id"], []).append(items)
                for a in items:
                    key = a["link"] if "news.google.com" in a["link"] else canon(a["link"])
                    if key in articles:  # same story listed in several sections
                        cats = articles[key]["categories"]
                        cats += [c for c in a["categories"] if c not in cats]
                    else:
                        articles[key] = a
                st["ok"] += 1
            except Exception as e:  # one bad feed should never break the app
                st["failed"] += 1
                print(f"  ! {s['name']} ({f['category']}): {e}")

        # Front pages: put stories in the same order (and with the same headlines) as the real site
        for s in config["sources"]:
            st = status.setdefault(s["id"], {"ok": 0, "failed": 0})
            front, st["frontpage"] = None, "feed"
            if s["id"] in home_futures:
                try:
                    front = home_futures[s["id"]].result()
                    st["frontpage"] = "live"
                except Exception as e:
                    print(f"  ! {s['name']} homepage: {e} (using feed order)")
            if front is None:
                lists = feed_lists.get(s["id"], [])
                if s.get("frontpage_order") == "newest":   # no homepage feed: newest across all its feeds
                    merged = sorted((a for l in lists for a in l), key=lambda a: a["published"] or 0, reverse=True)
                else:                                     # the site's own top-stories feed, in its order
                    merged = lists[0] if lists else []
                front, seen = [], set()
                for a in merged:
                    t = re.sub(r"\W+", "", a["title"].lower())
                    if t not in seen:
                        seen.add(t)
                        front.append({"title": a["title"], "link": a["link"], "image": a["image"]})
            # skip old evergreen/archive pieces some homepages promote further down (e.g. NPR features from 2020)
            now = time.time()
            front = [h for h in front if (date_from_url(h["link"]) or now) > now - FRONTPAGE_MAX_AGE][:40]
            for rank, h in enumerate(front, 1):
                key = h["link"] if "news.google.com" in h["link"] else canon(h["link"])
                a = articles.get(key)
                if a is None:
                    a = articles[key] = {"title": h["title"], "link": h["link"], "summary": "", "image": None,
                                         "published": date_from_url(h["link"]), "date_only": True,
                                         "source": s["id"], "categories": []}
                a["title"] = h["title"]          # the site's own front-page headline
                a["image"] = a["image"] or h["image"]
                a["rank"] = rank
                if "Top" not in a["categories"]:
                    a["categories"].insert(0, "Top")
        # stories only in a "Top" RSS feed but no longer on the live front page aren't front-page stories
        for a in articles.values():
            if "rank" not in a and status[a["source"]].get("frontpage") == "live" and a["categories"] == ["Top"]:
                a["categories"] = ["Latest"]

    now = time.time()
    items, seen_titles = [], set()
    for a in sorted(articles.values(), key=lambda a: ("news.google.com" in a["link"], a.get("rank") or 999)):
        if a["published"] and a["published"] > now + 3600:
            continue  # bad future dates
        if a["published"] and now - a["published"] > MAX_AGE and not a.get("rank"):
            continue  # stale, and no longer on the site's front page
        t = (a["source"], re.sub(r"\W+", "", a["title"].lower()))
        if t in seen_titles:
            continue  # same story via Google News and the outlet's own feed
        seen_titles.add(t)
        items.append(a)
    items.sort(key=lambda a: a["published"] or 0, reverse=True)
    sources = [{k: s.get(k) for k in ("id", "name", "color", "homepage", "frontpage_order")} | status.get(s["id"], {})
               for s in config["sources"]]
    for s in config["sources"]:  # hosts we may look up thumbnails on
        site = s.get("homepage") or ("" if s.get("via_google") else s["feeds"][0]["url"])
        if site:
            _allowed_hosts.add(frontpage.site_key(site))
    return {"fetched": now, "sources": sources, "articles": items}


_allowed_hosts = set()
_thumbs = {}


def thumbnail(article_url):
    """Look up a story's share image (og:image) for stories that came without one."""
    if article_url in _thumbs:
        return _thumbs[article_url]
    img = None
    host = urlparse(article_url).netloc
    if urlparse(article_url).scheme in ("http", "https") and any(k in host for k in _allowed_hosts):
        try:
            req = urllib.request.Request(article_url, headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as r:
                head = r.read(300_000).decode("utf-8", "replace")
            m = (re.search(r'<meta[^>]+(?:property|name)="(?:og:image|twitter:image)"[^>]+content="([^"]+)"', head)
                 or re.search(r'<meta[^>]+content="([^"]+)"[^>]+(?:property|name)="(?:og:image|twitter:image)"', head))
            if m:
                img = html.unescape(m.group(1))
        except Exception:
            pass
    _thumbs[article_url] = img
    return img


def _reload():
    t = time.time()
    data = load_all()
    _cache["data"], _cache["time"] = data, time.time()
    print(f"Loaded {len(data['articles'])} articles in {time.time() - t:.1f}s")


def get_news(force=False):
    """Return cached news. When it's stale, refresh in the background and keep serving the old copy."""
    if force or not _cache["data"]:
        with _lock:
            if force or not _cache["data"]:
                _reload()
    elif time.time() - _cache["time"] > CACHE_SECONDS and _lock.acquire(blocking=False):
        _cache["time"] = time.time()  # don't start a second refresh meanwhile
        def run():
            try:
                _reload()
            finally:
                _lock.release()
        threading.Thread(target=run, daemon=True).start()
    return _cache["data"]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT / "static"), **kw)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/api/news", "/news.json"):
            force = parse_qs(url.query).get("refresh") == ["1"]
            body = json.dumps(get_news(force)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/thumb":
            img = thumbnail(parse_qs(url.query).get("u", [""])[0])
            self.send_response(302 if img else 404)
            if img:
                self.send_header("Location", img)
                self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
        else:
            super().do_GET()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=get_news, daemon=True).start()  # warm the cache
    print(f"NewsHub running at http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
