"""Telefonnummern: aus dem Inseratstext und aus einem Anbieter-Telefonbuch (z.B. immoboerse-dd.de)."""
import json
import re

from bs4 import BeautifulSoup
from curl_cffi import requests

from .config import DATA_DIR

PHONEBOOK = DATA_DIR / "phonebook.json"

# deutsche Rufnummern: 0351 / 86 29 29 0, +49 172 1614663, (0351) 258 566-30 …
PHONE = re.compile(r"(?<![\d.,])(?:\+49|0049|0)[\s(]*\d{2,5}\)?(?:[\s/\-–.]*\d){4,11}(?![\d.,]*\s*(?:€|EUR|m²|qm))")
# Kontext, in dem eine Ziffernfolge keine Telefonnummer ist
NOT_PHONE = re.compile(r"(?:ID|Nr\.?|Nummer|Objekt|Kennung|Referenz|IBAN|BIC|PLZ|Fax)\s*[:#]?\s*$", re.I)


def normalize(nr: str) -> str | None:
    digits = re.sub(r"\D", "", nr)
    if digits.startswith("0049"):
        digits = "0" + digits[4:]
    elif digits.startswith("49") and nr.strip().startswith("+"):
        digits = "0" + digits[2:]
    if not digits.startswith("0") or digits.startswith("00") or not 9 <= len(digits) <= 13:
        return None
    if re.fullmatch(r"01[0-4]\d\d\d*", digits) and len(digits) <= 9:
        return None  # eher PLZ + Hausnummer o.ä.
    # lesbar als "Vorwahl Rufnummer": Mobil (01xx) und z.B. 0351 vierstellig, Großstädte wie 040 dreistellig
    pre = 3 if digits.startswith(("030", "040", "069", "089")) else 4
    return f"{digits[:pre]} {digits[pre:]}"


def from_text(text: str | None) -> list[str]:
    """Telefonnummern aus Freitext (ohne Faxnummern, IDs, Preise)."""
    out = []
    for m in PHONE.finditer(text or ""):
        before = text[max(0, m.start() - 20):m.start()]
        if NOT_PHONE.search(before) or re.search(r"fax", before, re.I):
            continue
        n = normalize(m.group())
        if n and n not in out:
            out.append(n)
    return out[:3]


# ---------- Telefonbuch der Anbieter ----------

def scrape_immoboerse() -> dict:
    """Antje Schmidt Immobilien (immoboerse-dd.de): Zentrale + Ansprechpartner mit Mobilnummer."""
    r = requests.get("https://www.immoboerse-dd.de/", impersonate="chrome", timeout=30)
    r.raise_for_status()
    t = " ".join(BeautifulSoup(r.text, "lxml").get_text(" ").split())
    people = []
    # "… Julian Klunker Makler Vermietung & Ausbilder Tel.: 0172 / 161 46 63 Mail: …"
    for m in re.finditer(r"([A-ZÄÖÜ][a-zäöüß]+ [A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?) ([^.“”\"]{3,80}?) Tel\.?:\s*([\d\s/–\-]{8,})", t):
        nr = normalize(m.group(3))
        if nr and not re.search(r"Datenschutz|Impressum|Kontakt|Cookie|Telefon", m.group(1)):
            people.append({"name": m.group(1), "role": m.group(2).strip(), "phone": nr})
    main = re.search(r"Barbarastraße.{0,40}?Tel\.?:\s*([\d\s/–\-]{8,})", t)
    return {
        "name": "Antje Schmidt Immobilien",
        "match": ["antje schmidt", "immoboerse"],
        "phone": normalize(main.group(1)) if main else None,
        "people": people,
        "source": "immoboerse-dd.de",
    }


def refresh_phonebook(log=print) -> list[dict]:
    book = []
    for fn in (scrape_immoboerse,):
        try:
            book.append(fn())
        except Exception as e:  # Telefonbuch ist ein Zusatz – Fehler sollen den Lauf nicht stoppen
            log(f"Telefonbuch {fn.__name__}: {e}")
    if book:
        PHONEBOOK.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
        _cache.clear()
    return book


_cache: dict = {}


def phonebook() -> list[dict]:
    if "book" not in _cache:
        _cache["book"] = json.loads(PHONEBOOK.read_text(encoding="utf-8")) if PHONEBOOK.exists() else []
    return _cache["book"]


def for_listing(item: dict) -> list[dict]:
    """Alle Nummern zu einem Inserat: [{"nr": "...", "label": "..."}], beste zuerst."""
    text = " ".join(str(item.get(k) or "") for k in ("provider", "title", "description", "raw_text"))
    low = text.lower()
    out = []

    def add(nr, label):
        if nr and all(o["nr"] != nr for o in out):
            out.append({"nr": nr, "label": label})

    for entry in phonebook():
        if any(m in low for m in entry["match"]):
            # genannte Ansprechperson -> deren Nummer zuerst (Firmenname vorher entfernen: "Antje Schmidt Immobilien")
            persons_text = low.replace(entry["name"].lower(), " ")
            for p in entry["people"]:
                if p["name"].lower() in persons_text:
                    add(p["phone"], f"{p['name']} ({entry['name']})")
            add(entry.get("phone"), f"{entry['name']} (Zentrale)")
    for nr in item.get("phones_found") or []:
        add(nr, "aus dem Inserat")
    for nr in from_text(text):
        add(nr, "aus dem Inserat")
    return out
