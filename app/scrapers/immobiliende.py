"""immobilien.de – Mietwohnungen Dresden."""
import re

from bs4 import BeautifulSoup

from .base import Scraper, clean, is_dresden, node_text

BASE = "https://www.immobilien.de"
SEARCH = BASE + "/mieten/wohnung/dresden/?page={page}"


class ImmobilienDe(Scraper):
    name = "immobiliende"
    label = "immobilien.de"

    def search(self, page):
        s = self.soup(SEARCH.format(page=page))
        out, seen = [], set()
        for a in s.select('a[href^="/expose/"]'):
            eid = a["href"].split("/")[2]
            if eid in seen or not eid.isdigit():
                continue
            seen.add(eid)
            out.append({"ext_id": eid, "url": BASE + "/expose/" + eid})
        return out

    def detail(self, item):
        r = self.fetcher.get(item["url"])
        if r.status_code >= 400:
            return {"active": False}
        s = BeautifulSoup(r.text, "lxml")
        main = s.select_one("main") or s.body
        for junk in main.select("script, style, svg, form, nav, footer"):
            junk.decompose()
        text = clean(main.get_text("\n"))
        # Empfehlungen anderer Inserate (auch aus anderen Städten) abschneiden
        text = re.split(r"\nWeitere Angebote (?:von|in) ", text)[0]
        out = {"title": node_text(s.select_one("h1")) or None, "raw_text": text[:15000]}
        if m := re.search(r"\b(0\d{4})\s+Dresden", text):
            out["zip"] = m.group(1)
        if out.get("zip") and not is_dresden(out["zip"]):
            return {"active": False}
        if m := re.search(r"Beschreibung der Immobilie\n(.+?)\n(?:Ausstattungsmerkmale|Energie|Lage & Infrastruktur|Sonstige)", text, re.S):
            out["description"] = clean(m.group(1))
        if m := re.search(r"Weitere Angebote von ([^\n]+)", clean(s.get_text("\n"))):
            out["provider"] = m.group(1).strip()
        if m := re.search(r"Dresden(?::\s*|-)([A-ZÄÖÜ][\wäöüß]+(?:[ /-][A-ZÄÖÜ][\wäöüß]+)?)", out["title"] or ""):
            out["district"] = m.group(1)
        imgs = []
        for u in re.findall(r'https://immobilien-api\.fly\.dev/api/images/estates/[^"\'\s?]+\.(?:jpe?g|png|webp)', r.text):
            if u not in imgs:
                imgs.append(u)
        out["images"] = imgs or None
        return out
