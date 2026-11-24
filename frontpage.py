"""Scrape an outlet's homepage and return its stories in the order the site shows them."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

SKIP_TAGS = {"nav", "footer", "script", "style", "noscript", "svg", "template"}
SKIP_CLASS = re.compile(r"(^|[\s_-])(menu|nav|navigation|dropdown|subnav|more-subsection|footer|sidebar-nav|breadcrumbs?)($|[\s_-])", re.I)
VOID = {"img", "source", "br", "meta", "link", "input", "hr", "wbr", "area", "col", "embed", "param", "track"}
HEAD_TAGS = {"h1", "h2", "h3", "h4", "h5"}
HEADLINE_HINT = re.compile(r"headline|title|(^|[\s_-])hed($|[\s_-])|card-head", re.I)
ARTICLE_PATH = re.compile(
    r"/20\d\d/(\d\d|[a-z]{3})/|/articles?/|story\?id=|/wirestory/|/live-updates/|-rcna\d+|/\d{6,}/|\d{7,}", re.I)
NOT_ARTICLE = re.compile(
    r"/videos?/|/live-tv|/podcasts?/|/newsletters?|/author/|/profiles?/|/games/|/series/|/shows?/|/shop|/deals?/|/tag/|/topics?/", re.I)


def _clean(t):
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*\b20\d\d-\d\d-\d\dT[\d:.]+Z?", "", t)  # ISO timestamps
    return re.sub(r"\s*(Updated|Published)?\s*\d+\s*(m|h|min|mins|hrs?|hours?) ago$", "", t).strip()


class _Parser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self.order = base, {}, []
        self.stack = []      # open elements: dicts with tag, skip, head(buffer or None)
        self.anchor = None

    def _skipping(self):
        return any(e["skip"] for e in self.stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "") + " " + (a.get("data-testid") or "")
        if tag in ("img", "source"):
            if self.anchor and not self.anchor["img"]:
                src = a.get("src") or a.get("data-src") or (a.get("srcset") or a.get("data-srcset") or "").split(" ")[0]
                if src and not src.startswith("data:") and ".svg" not in src:
                    self.anchor["img"] = urljoin(self.base, src)
            return
        if tag in VOID:
            return
        hint = self.anchor is not None and (tag in HEAD_TAGS or bool(HEADLINE_HINT.search(cls)))
        el = {"tag": tag, "skip": tag in SKIP_TAGS or bool(SKIP_CLASS.search(a.get("class") or "")),
              "head": [] if hint else None}
        self.stack.append(el)
        if tag == "a" and a.get("href"):
            # an <a> sitting directly inside a heading is itself a headline
            parent_head = any(e["tag"] in HEAD_TAGS for e in self.stack[-3:-1])
            self.anchor = {"href": urljoin(self.base, a["href"]).split("#")[0], "aria": a.get("aria-label") or "",
                           "text": [], "heads": [], "img": None, "el": el, "in_head": parent_head}

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        # pop up to and including the matching tag (tolerates sloppy HTML)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                for el in self.stack[i:][::-1]:
                    if el["head"] is not None and self.anchor:
                        self.anchor["heads"].append(_clean(" ".join(el["head"])))
                    if self.anchor and el is self.anchor["el"]:
                        if not self._skipping_above(i):
                            self._close(self.anchor)
                        self.anchor = None
                del self.stack[i:]
                break

    def _skipping_above(self, i):
        return any(e["skip"] for e in self.stack[:i + 1])

    def handle_data(self, data):
        if self.anchor is None:
            return
        self.anchor["text"].append(data)
        for el in self.stack:
            if el["head"] is not None:
                el["head"].append(data)

    def _close(self, a):
        full = _clean(" ".join(a["text"]))
        heads = sorted((h for h in a["heads"] if 20 <= len(h) <= 220), key=len)
        candidates = [(4, _clean(a["aria"])), (3, full if a["in_head"] else ""), (2, heads[0] if heads else ""), (1, full)]
        e = self.links.get(a["href"])
        if not e:
            e = self.links[a["href"]] = {"link": a["href"], "title": "", "score": 0, "image": None}
            self.order.append(a["href"])
        for score, t in candidates:
            if 20 <= len(t) <= 220 and score > e["score"]:
                e["title"], e["score"] = t, score
                break
        e["image"] = e["image"] or a["img"]


def site_key(url):
    labels = urlparse(url).netloc.split(".")
    return max((l for l in labels if l not in ("www", "com", "org", "co", "uk", "net", "go")), key=len)


def extract(page_html, base_url, limit=40):
    p = _Parser(base_url)
    p.feed(page_html)
    key = site_key(base_url)
    out, seen = [], set()
    for href in p.order:
        e = p.links[href]
        u = urlparse(href)
        path = u.path + ("?" + u.query if u.query else "")
        if key not in u.netloc or not e["title"] or NOT_ARTICLE.search(u.path):
            continue
        if not ARTICLE_PATH.search(path) and u.path.rstrip("/").rsplit("/", 1)[-1].count("-") < 3:
            continue
        canon = u.netloc.replace("www.", "") + u.path.rstrip("/")
        if "id=" in u.query and "update=" not in u.query:
            canon += "?" + u.query
        if canon in seen:
            continue
        seen.add(canon)
        out.append({"title": e["title"], "link": href.split("?update=")[0], "image": e["image"]})
        if len(out) >= limit:
            break
    return out
