"""ohne-makler.net – private Vermieter."""
import html as htmllib
import re

from ..extract import num
from .base import Scraper, clean, node_text

BASE = "https://www.ohne-makler.net"
SEARCH = BASE + "/immobilien/wohnung-mieten/sachsen/dresden/?page={page}"


class OhneMakler(Scraper):
    name = "ohnemakler"
    label = "ohne-makler"

    def search(self, page):
        s = self.soup(SEARCH.format(page=page))
        out = []
        for a in s.select("a[data-om-id]"):
            if a.get("data-om-type") != "RENT":
                continue
            img = a.select_one("img")
            out.append({
                "ext_id": a["data-om-id"], "url": BASE + a["href"],
                "title": node_text(a.select_one("h4")) or (img.get("alt") if img else None),
                "price_cold": num(node_text(a.select_one(".text-primary-500"))),
                "images": [img["src"]] if img and img.get("src") else None,
            })
        return out

    def detail(self, item):
        r = self.fetcher.get(item["url"])
        if r.status_code == 404:
            return {"active": False}
        from bs4 import BeautifulSoup
        s = BeautifulSoup(r.text, "lxml")
        main = s.select_one("main") or s.body
        for junk in main.select("script, style, svg, form, nav, footer, dialog"):
            junk.decompose()
        text = clean(main.get_text("\n"))
        # Ähnliche Angebote am Seitenende abschneiden
        text = re.split(r"\n(?:Ähnliche (?:Immobilien|Angebote)|Weitere Angebote|Das könnte Sie auch interessieren)", text)[0]
        imgs = []
        for u in re.findall(r'https://media\.ohne-makler\.net/rs:fit:1920:1080/[^"\'\s]+', r.text):
            u = htmllib.unescape(u)
            if u not in imgs:
                imgs.append(u)
        out = {"title": node_text(s.select_one("h1")) or None, "raw_text": text[:15000], "images": imgs or None,
               "provider": "Privat (ohne-makler)"}
        if m := re.search(r"\b(01[0-4]\d\d)\s+Dresden(?:\s*[-,(]\s*([A-ZÄÖÜ][\wäöüß\-/ ]+))?", text):
            out["zip"] = m.group(1)
            out["district"] = (m.group(2) or "").strip() or None
        elif re.search(r"\b0\d{4}\s+[A-ZÄÖÜ]", text[:3000]):
            return {"active": False}  # Umland (Suche liefert auch Orte um Dresden)
        if m := re.search(r"Beschreibung\n(.{50,4000}?)\n(?:Ausstattung|Lage|Sonstiges|Energie)", text, re.S):
            out["description"] = clean(m.group(1))
        return out
