"""SQLite-Speicher für Inserate."""
import json
import sqlite3
import threading
import time

from .config import DB_PATH

# Felder, die aus Scraping/Extraktion kommen. Nutzerfelder (favorite, hidden, ...) werden nie überschrieben.
DATA_FIELDS = {
    "source": "TEXT", "ext_id": "TEXT", "url": "TEXT", "title": "TEXT", "description": "TEXT",
    "address": "TEXT", "zip": "TEXT", "district": "TEXT", "provider": "TEXT",
    "price_cold": "REAL", "price_warm": "REAL", "costs_extra": "REAL", "costs_heat": "REAL", "deposit": "REAL",
    "area": "REAL", "rooms": "REAL", "floor": "INTEGER", "floors_total": "INTEGER", "year_built": "INTEGER",
    "balcony": "INTEGER", "terrace": "INTEGER", "garden": "INTEGER", "kitchen": "INTEGER", "elevator": "INTEGER",
    "furnished": "INTEGER", "wbs_required": "INTEGER", "temporary": "INTEGER", "swap": "INTEGER", "available_from": "TEXT",
    "images": "TEXT", "building_type": "TEXT", "summary": "TEXT", "raw_text": "TEXT", "published": "TEXT",
    "plattenbau_score": "INTEGER", "plattenbau_reasons": "TEXT",
    "phones": "TEXT",  # [{"nr": "0351 8629290", "label": "aus dem Inserat"}, …]
}
STATE_FIELDS = {
    "first_seen": "REAL", "last_seen": "REAL", "active": "INTEGER DEFAULT 1",
    "detail_done": "INTEGER DEFAULT 0", "llm_done": "INTEGER DEFAULT 0", "llm_fields": "TEXT",
    # Rohdaten je Stufe, damit die Extraktion jederzeit ohne neues Laden wiederholt werden kann
    "search_fields": "TEXT", "detail_fields": "TEXT",
    "checked_at": "REAL",  # letzte gezielte Online-Prüfung der Inseratsseite
}
USER_FIELDS = {
    "favorite": "INTEGER DEFAULT 0", "hidden": "INTEGER DEFAULT 0",
    "status": "TEXT", "note": "TEXT", "seen": "INTEGER DEFAULT 0",
    "rating": "INTEGER",  # 1 = Like, -1 = Dislike (Swipe-Ansicht)
    "reason": "TEXT",  # warum nicht gefallen: "zu teuer, Lage; Freitext"
    "user_gone": "INTEGER DEFAULT 0",  # vom Nutzer als "nicht mehr online/vergeben" gemeldet -> bleibt offline
}
JSON_FIELDS = {"images", "plattenbau_reasons", "llm_fields", "search_fields", "detail_fields", "phones"}

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")


def init():
    with _lock:
        _conn.execute("CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY)")
        have = {r[1] for r in _conn.execute("PRAGMA table_info(listings)")}
        for name, typ in {**DATA_FIELDS, **STATE_FIELDS, **USER_FIELDS}.items():
            if name not in have:
                _conn.execute(f"ALTER TABLE listings ADD COLUMN {name} {typ}")
        # Altbestand ohne gespeicherte Exposé-Rohdaten nochmal laden
        _conn.execute("UPDATE listings SET detail_done=0 WHERE detail_done=1 AND detail_fields IS NULL AND active=1")
        _conn.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started REAL, finished REAL, log TEXT)")
        _conn.commit()


def _to_db(k, v):
    if k in JSON_FIELDS and v is not None and not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        # einzelne UTF-16-Surrogate (kaputte Emojis aus Portal-JSON) zusammensetzen bzw. ersetzen
        v = v.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return v


def row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_FIELDS:
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def get(listing_id: str) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
    return row_to_dict(r) if r else None


def upsert(item: dict) -> bool:
    """Fügt ein Inserat ein oder aktualisiert es. Leere Werte überschreiben vorhandene nicht.
    Gibt True zurück, wenn das Inserat neu ist."""
    lid = f"{item['source']}:{item['ext_id']}"
    now = time.time()
    with _lock:
        exists = _conn.execute("SELECT 1 FROM listings WHERE id=?", (lid,)).fetchone()
        fields = {k: _to_db(k, v) for k, v in item.items() if k in DATA_FIELDS and v not in (None, "", [])}
        fields["search_fields"] = _to_db("search_fields", {k: v for k, v in item.items() if v not in (None, "", [])})
        if not exists:
            fields.update(id=lid, first_seen=now, last_seen=now, active=1)
            cols = ",".join(fields)
            _conn.execute(f"INSERT INTO listings ({cols}) VALUES ({','.join('?' * len(fields))})", list(fields.values()))
        else:
            # Spalten werden aus search/detail/llm-Feldern neu berechnet (pipeline.refresh), hier nur Rohdaten + Zustand
            fields = {"search_fields": fields["search_fields"], "last_seen": now}
            sets = ",".join(f"{k}=?" for k in fields)
            # wieder gesehen = wieder aktiv, außer der Nutzer hat es als vergeben gemeldet
            _conn.execute(f"UPDATE listings SET {sets}, active = CASE WHEN user_gone=1 THEN 0 ELSE 1 END WHERE id=?",
                          [*fields.values(), lid])
        _conn.commit()
    return not exists


def update(listing_id: str, **fields):
    if not fields:
        return
    with _lock:
        sets = ",".join(f"{k}=?" for k in fields)
        _conn.execute(f"UPDATE listings SET {sets} WHERE id=?", [*(_to_db(k, v) for k, v in fields.items()), listing_id])
        _conn.commit()


def query(where: str = "1=1", params=(), columns: str = "*") -> list[dict]:
    with _lock:
        rows = _conn.execute(f"SELECT {columns} FROM listings WHERE {where}", params).fetchall()
    return [row_to_dict(r) for r in rows]


def mark_inactive(source: str, seen_ids: set[str]):
    with _lock:
        ids = [r[0] for r in _conn.execute("SELECT id FROM listings WHERE source=? AND active=1", (source,))]
        gone = [i for i in ids if i not in seen_ids]
        _conn.executemany("UPDATE listings SET active=0 WHERE id=?", [(i,) for i in gone])
        _conn.commit()
    return len(gone)


def mark_stale(source: str, older_than: float) -> int:
    """Inserate, die seit older_than (Unix-Zeit) in keiner Ergebnisliste mehr auftauchten, als offline markieren."""
    with _lock:
        n = _conn.execute("UPDATE listings SET active=0 WHERE source=? AND active=1 AND last_seen < ?",
                          (source, older_than)).rowcount
        _conn.commit()
    return n


def log_run(started: float, log: list[str]):
    with _lock:
        _conn.execute("INSERT INTO runs (started, finished, log) VALUES (?,?,?)", (started, time.time(), "\n".join(log)))
        _conn.commit()


def last_run() -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None
