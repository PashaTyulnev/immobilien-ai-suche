"""Ablauf eines Suchlaufs: Ergebnisseiten -> Exposés -> Extraktion -> (optional) LLM."""
import threading
import time
import traceback

from . import db, images, llm
from .config import load_config
from .db import DATA_FIELDS
from .extract import DRESDEN_ZIP, extract_text, merge, plattenbau
from .http import Fetcher
from .scrapers import SCRAPERS
from .scrapers.base import SKIP_TITLE, Blocked, is_dresden

STATUS: dict = {"running": False, "started": None, "finished": None, "sources": {}, "llm": {}, "log": []}
_run_lock = threading.Lock()


def log(msg: str):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    STATUS["log"] = (STATUS["log"] + [line])[-200:]


EXTRACTED = [k for k in DATA_FIELDS if k not in ("source", "ext_id", "url", "raw_text")]


def compute(row: dict) -> dict:
    """Kombiniert die Stufen (Exposé > Ergebnisliste > Regex > LLM), ergänzt Stadtteil und Plattenbau-Score."""
    search = row.get("search_fields") or {}
    detail = row.get("detail_fields") or {}
    llm_data = dict(row.get("llm_fields") or {})
    special = {k: llm_data.pop(k, None) for k in ("building_type", "summary")}
    title = detail.get("title") or search.get("title") or row.get("title") or ""
    # Ja/Nein-Merkmale (Balkon, Garten …) erkennt das LLM zuverlässiger als die Wortsuche -> Vorrang vor Regex
    llm_bools = {k: v for k, v in llm_data.items() if isinstance(v, bool)}
    parts = [detail, search, llm_bools, extract_text(title + "\n" + (row.get("raw_text") or search.get("description") or "")), llm_data]
    area = merge(*parts).get("area")
    for p in parts:  # Platzhalterpreise (z.B. "100 €" bei Kleinanzeigen) verwerfen, dann greift die nächste Stufe
        for k in ("price_cold", "price_warm"):
            v = p.get(k)
            if v is not None and (not 150 <= v <= 15000 or (area and v / area < 4)):
                p[k] = None
    full = merge(*parts)
    full.update(special)
    if not full.get("district") and full.get("zip") in DRESDEN_ZIP:
        full["district"] = DRESDEN_ZIP[full["zip"]]
    full["plattenbau_score"], full["plattenbau_reasons"] = plattenbau(full)
    return full


def refresh(lid: str) -> dict:
    full = compute(db.get(lid))
    extra = {"active": 0} if full.get("zip") and not is_dresden(full["zip"]) else {}  # Umland aussortieren
    db.update(lid, **{k: full.get(k) for k in EXTRACTED}, **extra)
    return full


def run_source(name: str, scfg: dict, delay: float):
    sc = SCRAPERS[name](Fetcher(delay))
    st = STATUS["sources"][name] = {"label": sc.label, "phase": "Ergebnisseiten", "page": 0, "found": 0, "new": 0,
                                    "details": 0, "details_total": 0, "errors": 0, "gone": 0}
    seen, complete = set(), True
    for page in range(1, scfg.get("max_pages", 10) + 1):
        st["page"] = page
        try:
            items = sc.search(page)
        except Exception as e:
            if page > 1 and "HTTP 404" in str(e):
                break  # hinter der letzten Seite
            st["errors"] += 1
            complete = False
            log(f"{name}: Seite {page} fehlgeschlagen: {e}")
            break
        fresh = [i for i in items if f"{name}:{i['ext_id']}" not in seen]
        if not fresh:
            break
        for it in fresh:
            lid = f"{name}:{it['ext_id']}"
            seen.add(lid)
            if SKIP_TITLE.search(it.get("title") or ""):
                continue
            st["found"] += 1
            if db.upsert({**it, "source": name}):
                st["new"] += 1
            refresh(lid)
    else:
        complete = False  # max_pages erreicht -> nicht alles gesehen, nichts als verschwunden markieren
    if complete and seen and sc.tracks_removal:
        st["gone"] = db.mark_inactive(name, seen)
    log(f"{name}: {st['found']} Inserate, {st['new']} neu, {st['gone']} nicht mehr online")

    todo = db.query("source=? AND active=1 AND detail_done=0", (name,))
    if not scfg.get("details", True):
        # Exposés nicht laden (Portal sperrt schnell) – Ergebnisliste + Beschreibung reichen für Regex/LLM
        for row in todo:
            s = row.get("search_fields") or {}
            db.update(row["id"], raw_text="\n".join(x for x in (s.get("title"), s.get("description")) if x), detail_done=1)
            refresh(row["id"])
        todo = []
    st.update(phase="Exposés", details_total=len(todo))
    for row in todo:
        try:
            d = sc.detail(row)
            if d.get("active") is False or SKIP_TITLE.search(d.get("title") or ""):
                db.update(row["id"], active=0, detail_done=1)
            else:
                raw = d.pop("raw_text", None)
                db.update(row["id"], detail_fields={k: v for k, v in d.items() if v not in (None, "", [])},
                          raw_text=raw, detail_done=1)
                full = refresh(row["id"])
                images.prefetch(name, full.get("images"))
        except Blocked as e:
            st["errors"] += 1
            log(f"{name}: {e}")
            break
        except Exception as e:
            st["errors"] += 1
            log(f"{name}: Exposé {row['url']} fehlgeschlagen: {e}")
            if st["errors"] > 25:
                log(f"{name}: zu viele Fehler, breche ab")
                break
        st["details"] += 1
    st["phase"] = "fertig"


def run_llm(cfg: dict, scraping_done: threading.Event):
    st = STATUS["llm"] = {"phase": "prüfe Ollama", "done": 0, "todo": 0, "model": cfg["model"]}
    if not cfg.get("enabled") or not llm.available(cfg):
        st["phase"] = "deaktiviert" if not cfg.get("enabled") else f"Ollama/Modell {cfg['model']} nicht erreichbar"
        return
    st["phase"] = "läuft"
    while True:
        todo = db.query("active=1 AND detail_done=1 AND llm_done=0 ORDER BY first_seen DESC LIMIT 20")
        st["todo"] = len(db.query("active=1 AND detail_done=1 AND llm_done=0", columns="id"))
        if not todo:
            if scraping_done.is_set():
                break
            time.sleep(5)
            continue
        for row in todo:
            try:
                db.update(row["id"], llm_fields=llm.extract(cfg, row), llm_done=1)
                refresh(row["id"])
            except Exception as e:
                log(f"LLM-Fehler bei {row['id']}: {e}")
                db.update(row["id"], llm_done=1)
            st["done"] += 1
    st["phase"] = "fertig"


def run(only: list[str] | None = None):
    if not _run_lock.acquire(blocking=False):
        return False
    try:
        cfg = load_config()
        STATUS.update(running=True, started=time.time(), finished=None, sources={}, llm={})
        log("Suchlauf gestartet")
        done = threading.Event()
        llm_thread = threading.Thread(target=run_llm, args=(cfg["llm"], done), daemon=True)
        llm_thread.start()
        threads = []
        for name, scfg in cfg["sources"].items():
            if name in SCRAPERS and scfg.get("enabled") and (not only or name in only):
                t = threading.Thread(target=_guard, args=(run_source, name, scfg, scfg.get("delay", cfg["request_delay"])), daemon=True)
                t.start()
                threads.append(t)
        for t in threads:
            t.join()
        done.set()
        llm_thread.join()
        log("Suchlauf fertig")
        db.log_run(STATUS["started"], STATUS["log"])
    finally:
        STATUS.update(running=False, finished=time.time())
        _run_lock.release()
    return True


def _guard(fn, *args):
    try:
        fn(*args)
    except Exception:
        log(traceback.format_exc())


def reextract_all():
    """Alle Felder aus den gespeicherten Rohdaten neu berechnen (nach Änderungen an Regex/Heuristik)."""
    rows = db.query("search_fields IS NOT NULL", columns="id")
    for row in rows:
        refresh(row["id"])
    return len(rows)


def scheduler():
    """Startet automatisch Suchläufe im eingestellten Intervall."""
    while True:
        hours = load_config().get("auto_scrape_hours") or 0
        last = db.last_run()
        if hours and not STATUS["running"] and (not last or time.time() - last["finished"] > hours * 3600):
            threading.Thread(target=run, daemon=True).start()
        time.sleep(60)
