"""markt.de – Kleinanzeigen Mietwohnungen Dresden (die Liste mischt Anzeigen anderer Städte ein)."""
import re

from bs4 import BeautifulSoup

from .base import SKIP_TITLE, Scraper, check_blocked, clean, is_dresden, node_text

BASE = "https://www.markt.de"
SEARCH = BASE + "/dresden/immobilien/mietwohnungen/?page={page}"
NOT_FLAT = re.compile(r"garage|stellplatz|carport|lager|gewerbe|büro|laden|praxis|ferienwohnung|monteur|wg-zimmer|\bzimmer in\b", re.I)


class MarktDe(Scraper):
    name = "marktde"
    label = "markt.de"

    def search(self, page):
        s = self.soup(SEARCH.format(page=page))
        out = {}
        for a in s.select('a[href*="/a/"]'):
            m = re.search(r"/a/([0-9a-f]{6,})/", a["href"])
            if not m or m.group(1) in out:
                continue
            card = a
            for _ in range(6):  # Karte um den Link: Titel, Preis, "01069 Dresden"
                if card.parent is None or len(set(re.findall(r"/a/([0-9a-f]{6,})/", str(card.parent)))) > 1:
                    break
                card = card.parent
            text = " ".join(card.get_text(" ").split())
            zips = re.findall(r"\b(0\d{4})\s+[A-ZÄÖÜ]", text)
            title = node_text(card.select_one("h2, h3")) or a.get_text(" ", strip=True)
            if (zips and not any(is_dresden(z) for z in zips)) or SKIP_TITLE.search(title or "") or NOT_FLAT.search(title or ""):
                continue  # andere Stadt, Gesuch oder keine Wohnung
            url = a["href"] if a["href"].startswith("http") else BASE + a["href"]
            out[m.group(1)] = {"ext_id": m.group(1), "url": url.split("?")[0], "title": title or None,
                               "zip": next((z for z in zips if is_dresden(z)), None)}
        return list(out.values())

    def detail(self, item):
        r = self.fetcher.get(item["url"])
        if r.status_code in (404, 410):
            return {"active": False}
        s = BeautifulSoup(r.text, "lxml")
        check_blocked(s)
        imgs = list(dict.fromkeys(re.findall(r'<meta[^>]+property="og:image"[^>]+content="(https://imagecache\.markt\.de/[^"]+)"', r.text)))
        main = s.select_one("main, article, #content") or s.body
        for junk in main.select("script, style, nav, header, footer, form, aside, iframe"):
            junk.decompose()
        text = clean(main.get_text("\n"))
        zips = re.findall(r"\b(0\d{4})\s+[A-ZÄÖÜ]", text[:4000])
        title = node_text(s.select_one("h1")) or item.get("title")
        if (zips and not is_dresden(zips[0])) or SKIP_TITLE.search(title or "") or NOT_FLAT.search(title or ""):
            return {"active": False}
        return {"title": title, "raw_text": text[:15000], "description": text[:3000], "images": imgs or None}
