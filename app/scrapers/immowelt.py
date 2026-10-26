"""immowelt.de (enthält seit der Fusion auch die Immonet-Angebote)."""
import json
import re

import lzstring

from ..extract import floor_from, num
from .base import Scraper, clean, is_dresden

SEARCH = ("https://www.immowelt.de/classified-search?distributionTypes=Rent&estateTypes=Apartment"
          "&locations=AD08DE9991&order=DateDesc&page={page}")


def _ufrn(html: str) -> dict:
    m = re.search(r'window\["__UFRN_FETCHER__"\]=JSON\.parse\((".*?")\);', html, re.S)
    return json.loads(json.loads(m.group(1)))["data"] if m else {}


class Immowelt(Scraper):
    name = "immowelt"
    label = "Immowelt/Immonet"

    def search(self, page):
        r = self.fetcher.get(SEARCH.format(page=page))
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = _ufrn(r.text).get("classified-serp-init-data")
        if not data:
            return []
        props = json.loads(lzstring.LZString().decompressFromBase64(data))["pageProps"]
        if page > 1 and props.get("page") != page:  # über das Ende hinaus
            return []
        wanted = set(props.get("classifieds") or [])
        out = []
        for cid, c in (props.get("classifiedsData") or {}).items():
            if wanted and cid not in wanted:
                continue
            addr = (c.get("location") or {}).get("address") or {}
            if not is_dresden(addr.get("zipCode")):
                continue
            hf = c.get("hardFacts") or {}
            prices = {p.get("label"): num(p.get("ariaLabel")) for p in hf.get("prices") or []}
            facts = {f.get("type"): f.get("splitValue") or f.get("value") for f in hf.get("facts") or []}
            raw = c.get("rawData") or {}
            desc = c.get("mainDescription") or {}
            prov = (c.get("farbwelt") or {}).get("providerName") or (c.get("cardProvider") or {}).get("title")
            out.append({
                "ext_id": cid,
                "url": c.get("url"),
                "title": desc.get("headline") or hf.get("title"),
                "description": clean(desc.get("description")),
                "address": ", ".join(x for x in (addr.get("street"), f"{addr.get('zipCode', '')} {addr.get('city', '')}".strip()) if x),
                "zip": addr.get("zipCode"),
                "district": addr.get("district"),
                "provider": clean(prov),
                "price_cold": prices.get("Kaltmiete"),
                "price_warm": prices.get("Warmmiete"),
                "area": (raw.get("surface") or {}).get("main") or num(facts.get("livingSpace")),
                "rooms": raw.get("nbroom") or num(facts.get("numberOfRooms")),
                "floor": floor_from(facts.get("numberOfFloors")),
                "images": [i["url"] for i in (c.get("gallery") or {}).get("images") or [] if i.get("url")],
                "published": (c.get("metadata") or {}).get("creationDate"),
            })
        return out

    def detail(self, item):
        s = self.soup(item["url"])
        main = s.select_one('[data-testid="aviv.CDP.main"]') or s.body
        for junk in main.select('[data-testid*="Contacting"], [data-testid^="cdp-contact"], script, style, svg'):
            junk.decompose()
        parts = []
        for sel in ('[data-testid="cdp-classified-keys"]', '[data-testid="cdp-features"]', '[data-testid="cdp-additional-costs"]',
                    '[data-testid="cdp-energy"]', ".DescriptionTexts", '[data-testid="cdp-additional-description-expandable-text"]'):
            for n in main.select(sel):
                parts.append(n.get_text("\n"))
        text = clean("\n".join(parts)) or clean(main.get_text("\n"))[:15000]
        desc = " ".join(clean(n.get_text(" ")) for n in main.select(".DescriptionTexts"))
        return {"raw_text": text, "description": desc or None}
