"""kleinanzeigen.de – Mietwohnungen Dresden."""
import json
import re

from .. import phones
from ..extract import floor_from, num
from .base import SKIP_TITLE, Scraper, clean, node_text

BASE = "https://www.kleinanzeigen.de"
SEARCH = BASE + "/s-wohnung-mieten/dresden/{page}c203l3820"

LABELS = {
    "Wohnfläche": "area", "Zimmer": "rooms", "Etage": "floor", "Warmmiete": "price_warm",
    "Nebenkosten": "costs_extra", "Heizkosten": "costs_heat", "Kaution / Genoss.-Anteile": "deposit",
    "Kaution": "deposit", "Verfügbar ab": "available_from", "Baujahr": "year_built",
}


class Kleinanzeigen(Scraper):
    name = "kleinanzeigen"
    label = "Kleinanzeigen"

    def search(self, page):
        s = self.soup(SEARCH.format(page=f"seite:{page}/" if page > 1 else ""))
        out = []
        for art in s.select("article[data-adid]"):
            href = art.get("data-href")
            if not href:
                continue
            ld = {}
            if (sc := art.select_one('script[type="application/ld+json"]')) and sc.string:
                try:
                    ld = json.loads(sc.string)
                except ValueError:
                    pass
            title = ld.get("title") or node_text(art.select_one("h2, h3"))
            if SKIP_TITLE.search(title or ""):
                continue
            # Eckdaten stehen schon auf der Karte ("510 €", "53,57 m² · 2 Zi.") -> Preis auch ohne Exposé
            price_tag = art.select_one("p.text-title3")
            card = " ".join(art.get_text(" ").split())
            area = re.search(r"([\d.,]+)\s*m²", card)
            rooms = re.search(r"([\d,]+)\s*Zi\.", card)
            out.append({
                "ext_id": art["data-adid"], "url": BASE + href, "title": title,
                "description": ld.get("description"),
                "images": [ld["contentUrl"].replace("$_59", "$_57")] if ld.get("contentUrl") else None,
                "price_cold": num(node_text(price_tag)) if price_tag and "€" in price_tag.get_text() else None,
                "area": num(area.group(1)) if area else None,
                "rooms": num(rooms.group(1)) if rooms else None,
            })
        # Kleinanzeigen zeigt nach der letzten Seite wieder Seite 1 -> erkennen über aktive Seitenzahl
        cur = s.select_one(".pagination-current, [aria-current='page']")
        if page > 1 and cur and cur.get_text(strip=True).isdigit() and int(cur.get_text(strip=True)) != page:
            return []
        return out

    def detail(self, item):
        s = self.soup(item["url"])
        if not s.select_one("#viewad-title, #viewad-price"):
            return {"active": False}
        if not s.select_one("li.addetailslist--detail"):
            # abgespeckte Seite (Session-abhängig) -> mit frischer Session nochmal
            self.fetcher.reset()
            s = self.soup(item["url"])
        out = {"title": node_text(s.select_one("#viewad-title")) or None}
        out["price_cold"] = num(node_text(s.select_one("#viewad-price")))
        loc = node_text(s.select_one("#viewad-locality"))
        if loc:
            out["address"] = loc
            m = re.match(r"(\d{5})\s+Dresden\s*-?\s*(.*)", loc)
            if m:
                out["zip"], out["district"] = m.group(1), m.group(2) or None
        facts = []
        for li in s.select("li.addetailslist--detail"):
            val = node_text(li.select_one(".addetailslist--detail--value"))
            key = clean(li.get_text(" ")).replace(val, "").strip()
            facts.append(f"{key}: {val}")
            f = LABELS.get(key)
            if key == "Tauschangebot":
                out["swap"] = val.strip().lower() not in ("kein tausch", "nein")
            elif f == "floor":
                out[f] = floor_from(val)
            elif f == "available_from":
                out[f] = val
            elif f:
                out[f] = num(val)
        tags = [node_text(t) for t in s.select("li.checktag")]
        tl = " ".join(tags).lower()
        out["balcony"] = True if "balkon" in tl else None
        out["kitchen"] = True if "einbauküche" in tl else None
        out["elevator"] = True if "aufzug" in tl else None
        out["garden"] = True if "garten" in tl else None
        desc = node_text(s.select_one("#viewad-description-text"))
        out["description"] = desc or None
        imgs = []
        for img in s.select(".galleryimage-element img, #viewad-image"):
            src = img.get("data-imgsrc") or img.get("src")
            if src and "img.kleinanzeigen.de" in src and src not in imgs:
                imgs.append(re.sub(r"rule=\$_\d+", "rule=$_57", src))
        out["images"] = imgs or None
        for sel in (".userprofile-vip a", ".userprofile-vip", "#viewad-contact .text-body-regular-strong"):
            name = node_text(s.select_one(sel))
            if len(name) > 2:
                out["provider"] = name.split("\n")[0]
                break
        # Telefonnummer im Kontaktkasten (gewerbliche Anbieter zeigen sie dort oft)
        out["phones_found"] = phones.from_text(node_text(s.select_one("#viewad-contact"))) or None
        pub = node_text(s.select_one("#viewad-extra-info"))
        if m := re.search(r"\d{2}\.\d{2}\.\d{4}", pub):
            out["published"] = m.group()
        out["raw_text"] = clean(f"{out.get('title')}\n{loc}\nPreis: {out['price_cold']} €\n" + "\n".join(facts)
                                + "\nAusstattung: " + ", ".join(tags) + "\n" + desc)
        return out
