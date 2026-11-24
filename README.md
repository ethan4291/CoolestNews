# NewsHub

Your all-in-one news reader: NYT, CNN, BBC, NPR, Washington Post, WSJ, The Guardian,
ABC, CBS, NBC, Fox, Al Jazeera, Politico, CNBC, The Verge, Ars Technica, TechCrunch and ESPN
in one feed.

## Run it

Double-click `start.command` in Finder, or:

```bash
python3 server.py
```

then open http://localhost:8777. Nothing to install (Python standard library only).

## Front pages that match the real sites

NewsHub reads each outlet's actual homepage, so in **Front page** mode the stories, their order and
their headlines match the site. Hover a source and click **only** to see just that outlet's front page.
A green dot next to a source means it's matched live.

NYT, WSJ, Washington Post, Politico and ESPN block automated reading of their homepages. NYT and ESPN
use their own homepage/top-headlines feed in its order (very close). Washington Post, Politico and WSJ
have no such feed, so they show their newest stories first. Switch to **Latest** for everything newest-first.

## Staying up to date

- Every site is re-checked every 3 minutes; the page pulls new stories every 2 minutes.
- Stories older than 3 days drop off unless they're still on that outlet's front page, and old archive
  pieces (more than a week old) that some homepages promote are skipped.
- If a site has a hiccup, NewsHub retries and keeps showing its last good copy instead of going blank.

## Features

- Categories: Top, World, US, Politics, Business, Tech, Science, Sports
- Click a source in the sidebar to hide/show it; ⌘-click to show only that source
- Search (press `/`), Esc to clear
- ☆ Save stories for later (Saved tab)
- Read stories dim; option to hide them
- Grid or list layout, light/dark mode
- ↻ forces an immediate refresh

All your settings, saved and read stories are kept in your browser.

## Adding sources

Edit `feeds.json`: add any RSS/Atom feed URL under a source, with a category.
For outlets without a working RSS feed (CNN, WSJ, Guardian today), use a Google News search feed:
`https://news.google.com/rss/search?q=site:example.com+when:2d&hl=en-US&gl=US&ceid=US:en`
and set `"via_google": true`. Restart the server after editing.

## Hosting it as a website (GitHub Pages)

GitHub Actions rebuilds the site every ~15 minutes (`build.py` fetches everything and writes
`site/news.json`) and publishes it with GitHub Pages. Saved and read stories stay in each
visitor's own browser.

1. Create a new **public** repository on github.com (e.g. `newshub`). Don't add a README.
2. Push this folder to it (commands below).
3. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. Go to the **Actions** tab, open **Update news**, click **Run workflow**.
5. Your site will be at `https://<your-username>.github.io/newshub/`.

Notes: GitHub's schedule can run late when busy, so the site is usually 15–30 minutes behind.
Scheduled runs pause after 60 days without a commit; re-enable them from the Actions tab.
If a site blocks GitHub's servers, NewsHub falls back to that outlet's feed.

