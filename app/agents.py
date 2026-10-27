"""Makler-Crawler: Dresdner Makler/Vermieter sammeln, Webseite finden, auf eigene Mietangebote prüfen.

Ergebnis in data/agents.json, Anzeige in der App unter "🏢 Makler". Ziel: Seiten finden, die eigene
Wohnungen anbieten, die (noch) nicht über die Portale kommen – Kandidaten für einen eigenen Scraper.
"""
import json
import re
import threading
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

from . import db
from .config import DATA_DIR

AGENTS = DATA_DIR / "agents.json"
STATUS = {"running": False, "done": 0, "total": 0, "current": None, "finished": None}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Feste Startliste: Vermieter mit eigenen Wohnungen, die selten auf den Portalen stehen
SEED = [
    {"name": "WiD Wohnen in Dresden", "website": "https://www.wid-dresden.de/", "covered": "eigene Quelle wid"},
    {"name": "WG Aufbau Dresden eG", "website": "https://www.wgaufbau-dresden.de/", "covered": "eigene Quelle wgaufbau"},
    {"name": "Wohnungsgenossenschaft Glückauf Süd Dresden eG", "website": "https://www.wgs-dresden.de/", "covered": "eigene Quelle wgs"},
    {"name": "Sächsische Wohnungsgenossenschaft Dresden eG", "website": "https://www.swg-dresden.de/", "covered": "eigene Quelle swg"},
    {"name": "Eisenbahner-Wohnungsbaugenossenschaft Dresden eG", "website": "https://www.ewg-dresden.de/", "covered": "eigene Quelle ewg"},
    {"name": "Wohnungsgenossenschaft Johannstadt eG", "website": "https://www.wgj.de/", "covered": "eigene Quelle wgj"},
    {"name": "Johannes & Partner Immobilien", "website": "https://www.dresdden.de/", "covered": "eigene Quelle dresdden.de"},
    {"name": "Antje Schmidt Immobilien", "website": "https://www.immoboerse-dd.de/"},
]
# Anbieter, die nicht gemeint sind: Portale, Großvermieter (Vonovia-Filter), Zeitwohnen, Tauschbörsen
SKIP = re.compile(r"vonovia|buwog|deutsche wohnen|tauschwohnung|homecompany|zeitwohn|wunderflats|ohne.?makler|"
                  r"immowelt|immoscout|kleinanzeigen|wg-gesucht|^privat|^frau |^herr ", re.I)
COMMERCIAL = re.compile(r"gmbh|immobilien|immoblien|verwaltung|wohnen|makler|\bag\b|\bkg\b|e\.\s?k|genossenschaft|"
                        r"hausverwalt|partner|real estate|grundbesitz|vermiet|property|management|immo\b", re.I)
# nur das Portal selbst (immobilien.de), nicht Makler-Domains wie beate-protze-immobilien.de
PORTALS = re.compile(r"(?:^|\.)immobilien\.de$|immowelt|immonet|immobilienscout|is24|kleinanzeigen|wg-gesucht|ohne-makler|"
                     r"immobilien-api|google|goo\.gl|youtube|youtu\.be|matterport|facebook|instagram", re.I)
LEGAL = re.compile(r"\b(gmbh|mbh|ag|kg|se|ug|co|ohg|gbr|e\.?\s?k\.?|eg|haftungsbeschränkt|kundenservice|standort|team)\b", re.I)

# Technik auf der Seite -> Hinweis, wie man an die Angebote kommt
TECH = [
    ("Immowelt-Modul (schon über Immowelt abgedeckt)", r"homepagemodul\.immowelt|immowelt\.de/(?:expose|profil|liste)"),
    ("ImmoScout-Modul (schon über ImmoScout-Mails abgedeckt)", r"immobilienscout24\.de/(?:expose|homepage|anbieter)|is24\.de"),
    ("onOffice", r"onoffice|smartsite\.onoffice"),
    ("FlowFact", r"flowfact"),
    ("immonex (WordPress)", r"immonex|inx-property"),
    ("Wohnungshelden", r"wohnungshelden"),
    ("Immomio", r"immomio"),
    ("Propstack", r"propstack"),
    ("Aareon/Wodis", r"aareon|wodis"),
    ("ivd24", r"ivd24"),
    ("Estate Office", r"estateoffice|estate-office"),
]
RENT_LINK = re.compile(r"miet|vermiet|wohnung|angebot|immobilien|objekt|expos|freie|leerstand|suche", re.I)
SALE_ONLY = re.compile(r"kaufen|verkauf|kaufpreis|eigentumswohnung", re.I)
# ein Angebot = Preis + Fläche (+ Zimmer) dicht beieinander
OFFER = re.compile(r"(?:\d{1,2}(?:[.,]5)?\s*(?:-\s*)?(?:Zi\.?|Zimmer|Raum|RW)\b.{0,120}?)?\d{2,3}(?:[.,]\d{1,2})?\s*m².{0,160}?\d{1,2}\.?\d{3}(?:,\d{2})?\s*€|"
                   r"\d{1,2}\.?\d{3}(?:,\d{2})?\s*€.{0,160}?\d{2,3}(?:[.,]\d{1,2})?\s*m²", re.I | re.S)
RENT_WORD = re.compile(r"kaltmiete|warmmiete|nettokalt|miete\s*:|monatsmiete|nebenkosten", re.I)


def provider_key(p: str) -> str:
    k = (p or "").lower().split(" - ")[0]
    k = LEGAL.sub(" ", k)
    return " ".join(re.sub(r"[^a-zäöüß0-9 &]", " ", k).split()[:4])


# ---------- 1. Makler sammeln ----------

def collect() -> list[dict]:
    agents = {provider_key(s["name"]): {**s, "found_via": "Startliste"} for s in SEED}
    rows = db.query("provider IS NOT NULL", columns="id,source,provider,url,raw_text,description")
    for r in rows:
        name = r["provider"].split(" - ")[0].strip(" ­")
        if not COMMERCIAL.search(name) or SKIP.search(name) or len(name) < 4:
            continue
        k = provider_key(name)
        if not k:
            continue
        a = agents.setdefault(k, {"name": name, "found_via": "Inserate", "listings": 0, "sources": set(), "sample_urls": []})
        a["listings"] = a.get("listings", 0) + 1
        a.setdefault("sources", set()).add(r["source"])
        if len(a.setdefault("sample_urls", [])) < 3:
            a["sample_urls"].append(r["url"])
        if not a.get("website"):
            a["website"] = _website_from_text(name, f"{r.get('raw_text') or ''} {r.get('description') or ''}")
            if a["website"]:
                a["website_via"] = "Inseratstext"
    for a in agents.values():
        a["sources"] = sorted(a.get("sources") or [])
    return list(agents.values())


def _tokens(name: str) -> list[str]:
    stop = {"immobilien", "immoblien", "dresden", "und", "der", "die", "das", "hausverwaltung", "verwaltung", "wohnen",
            "makler", "partner", "service", "management", "real", "estate", "gruppe", "group", "dresdner", "sachsen"}
    words = re.findall(r"[a-zäöüß0-9]+", provider_key(name))
    return [w for w in words if w not in stop and len(w) >= 3] or [w for w in words if len(w) >= 3]


def _website_from_text(name: str, text: str) -> str | None:
    """Webseite aus dem Inseratstext, deren Domain zum Anbieternamen passt."""
    toks = [t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss") for t in _tokens(name)]
    for u in re.findall(r"(?:https?://|www\.)[\w.-]+\.[a-z]{2,}", text, re.I):
        host = re.sub(r"^(https?://)?(www\.)?", "", u.lower())
        if PORTALS.search(host):
            continue  # Portal-Adresse im Inseratstext ist nicht die Webseite des Maklers
        if any(t in host for t in toks):
            return "https://" + ("www." + host if host.count(".") == 1 else host) + "/"
    return None


# ---------- 2. Webseite finden ----------

class Fetch:
    """Höflicher Abruf: robots.txt beachten, 1 s Abstand pro Host."""

    def __init__(self):
        self.s = requests.Session(impersonate="chrome")
        self.last = {}
        self.robots = {}

    def allowed(self, url: str) -> bool:
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(host + "/robots.txt", timeout=10)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except Exception:
                rp.parse([])
            self.robots[host] = rp
        return self.robots[host].can_fetch(UA, url)

    def get(self, url: str):
        if not self.allowed(url):
            return None
        host = urlparse(url).netloc
        wait = self.last.get(host, 0) + 1.0 - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last[host] = time.time()
        try:
            r = self.s.get(url, timeout=15)
            return r if r.status_code == 200 and "html" in r.headers.get("content-type", "html") else None
        except Exception:
            return None


def _guess_domains(name: str) -> list[str]:
    toks = [t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss") for t in _tokens(name)]
    full = [w.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
            for w in re.findall(r"[a-zäöüß0-9]+", provider_key(name)) if w not in {"und", "der", "die", "das"}]
    cands = []
    for parts in (full, toks, toks + ["immobilien"], toks + ["immobilien", "dresden"], toks + ["dresden"]):
        if parts:
            cands += ["-".join(parts), "".join(parts)]
    return list(dict.fromkeys(f"https://www.{c}.de/" for c in cands if 3 <= len(c) <= 50))[:8]


def _profile_website(f: Fetch, agent: dict) -> str | None:
    """immobilien.de-Exposé -> Maklerprofil -> JSON-LD 'sameAs' (Webseite des Maklers)."""
    for u in agent.get("sample_urls", []):
        if "immobilien.de/expose" not in u:
            continue
        r = f.get(u)
        m = r and re.search(r'href="(/makler/\d+)"', r.text)
        p = m and f.get("https://www.immobilien.de" + m.group(1))
        s = p and re.search(r'"sameAs":\["(https?://[^"]+)"', p.text)
        if s and "immobilien.de" not in s.group(1):
            return s.group(1)
    return None


def find_website(f: Fetch, agent: dict) -> None:
    if agent.get("website"):
        return
    if (w := _profile_website(f, agent)):
        agent["website"], agent["website_via"] = w, "immobilien.de-Profil"
        return
    toks = _tokens(agent["name"])
    for u in _guess_domains(agent["name"]):
        r = f.get(u)
        if not r:
            continue
        t = " ".join(BeautifulSoup(r.text, "lxml").get_text(" ").split()).lower()
        # Seite muss den Namen tragen und nach Dresden aussehen, sonst ist es ein anderer Betrieb
        if all(tok in t for tok in toks) and "dresden" in t:
            agent["website"], agent["website_via"] = str(r.url), "Name geraten + geprüft"
            return


# ---------- 3. Seite einordnen ----------

def classify(f: Fetch, agent: dict) -> None:
    site = agent.get("website")
    if not site:
        agent.update(status="keine Webseite gefunden")
        return
    home = f.get(site)
    if not home:
        agent.update(status="nicht erreichbar")
        return
    base = urlparse(str(home.url)).netloc
    queue, seen, pages = [str(home.url)], set(), []
    while queue and len(pages) < 13:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        r = home if u == str(home.url) else f.get(u)
        if not r:
            continue
        s = BeautifulSoup(r.text, "lxml")
        text = " ".join(s.get_text(" ").split())
        rent_hits = len(RENT_WORD.findall(text))
        offers = len(OFFER.findall(text))
        pages.append({"url": u, "offers": offers, "rent": rent_hits, "html": r.text})
        links = []
        for a in s.select("a[href]"):
            href = urljoin(u, a["href"]).split("#")[0]
            label = a.get_text(" ", strip=True) + " " + href
            if urlparse(href).netloc == base and RENT_LINK.search(label) and not SALE_ONLY.search(label) \
                    and not re.search(r"\.(pdf|jpe?g|png|zip|docx?)$", href, re.I):
                links.append(href)
        queue += [l for l in dict.fromkeys(links) if l not in seen][:20]
    html = " ".join(p["html"] for p in pages)
    tech = [name for name, pat in TECH if re.search(pat, html, re.I)]
    best = max(pages, key=lambda p: (p["offers"], p["rent"]), default=None)
    agent.update(tech=tech, pages_checked=len(pages), listing_url=best["url"] if best and best["offers"] else None,
                 offers=best["offers"] if best else 0)
    portal_only = tech and all("abgedeckt" in t for t in tech)
    if agent.get("covered"):
        agent["status"] = "schon als Quelle eingebaut"
    elif best and best["offers"] >= 2 and best["rent"] >= 1 and not portal_only:
        agent["status"] = "eigene Mietangebote"
    elif portal_only:
        agent["status"] = "nur Portal-Modul (schon abgedeckt)"
    elif tech:
        agent["status"] = "Makler-Software gefunden"
    elif any(p["rent"] for p in pages):
        agent["status"] = "Vermietungsseite ohne erkennbare Angebote"
    else:
        agent["status"] = "keine Mietangebote gefunden"


# ---------- Ablauf ----------

def crawl(log=print) -> list[dict]:
    STATUS.update(running=True, done=0, finished=None)
    try:
        agents = collect()
        STATUS["total"] = len(agents)
        f = Fetch()
        for a in agents:
            STATUS["current"] = a["name"]
            try:
                find_website(f, a)
                classify(f, a)
            except Exception as e:
                a["status"] = f"Fehler: {e}"[:120]
            a["checked_at"] = time.time()
            a.pop("sample_urls", None)
            STATUS["done"] += 1
        agents = _merge_same_site(agents)
        agents.sort(key=lambda a: (a.get("status") != "eigene Mietangebote", -(a.get("offers") or 0), a["name"].lower()))
        AGENTS.write_text(json.dumps(agents, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"Makler-Crawler: {len(agents)} Anbieter geprüft, "
            f"{sum(a.get('status') == 'eigene Mietangebote' for a in agents)} mit eigenen Mietangeboten")
        return agents
    finally:
        STATUS.update(running=False, current=None, finished=time.time())


def _merge_same_site(agents: list[dict]) -> list[dict]:
    """Gleiche Webseite = gleicher Anbieter (z.B. vertippter Name "Immoblien" in einem Inserat)."""
    out, by_site = [], {}
    for a in agents:
        site = re.sub(r"^https?://(www\.)?", "", (a.get("website") or "").lower()).rstrip("/")
        if site and site in by_site:
            b = by_site[site]
            b["listings"] = (b.get("listings") or 0) + (a.get("listings") or 0)
            b["sources"] = sorted(set(b.get("sources") or []) | set(a.get("sources") or []))
            continue
        if site:
            by_site[site] = a
        out.append(a)
    return out


def start(log=print) -> bool:
    if STATUS["running"]:
        return False
    threading.Thread(target=crawl, args=(log,), daemon=True).start()
    return True


def load() -> list[dict]:
    return json.loads(AGENTS.read_text(encoding="utf-8")) if AGENTS.exists() else []
