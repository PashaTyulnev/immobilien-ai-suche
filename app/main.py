"""Webserver: API + Frontend. Start: python -m app.main"""
import threading
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, images, match, pipeline
from .config import ROOT, load_config
from .scrapers import SCRAPERS

STATIC = ROOT / "static"
LIST_COLUMNS = ",".join(c for c in ["id", *db.DATA_FIELDS, *db.STATE_FIELDS, *db.USER_FIELDS] if c not in ("raw_text", "llm_fields", "search_fields", "detail_fields"))
USER_EDITABLE = set(db.USER_FIELDS)


@asynccontextmanager
async def lifespan(app):
    db.init()
    threading.Thread(target=pipeline.scheduler, daemon=True).start()
    yield


app = FastAPI(title="Wohnungssuche Dresden", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    # nie aus dem Browser-Cache, damit neue CSS/JS-Versionen (?v=…) sofort greifen
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/listings")
def listings(inactive: bool = False):
    # Favoriten/Likes auch offline mitliefern, damit sie mit "offline"-Hinweis sichtbar bleiben
    where = "1=1" if inactive else "active=1 OR favorite=1 OR rating=1"
    return db.query(where, columns=LIST_COLUMNS + ",substr(raw_text,1,3000) AS raw_excerpt")


@app.post("/api/phone/{lid:path}")
def find_phone(lid: str):
    """Inseratsseite neu laden und Telefonnummer suchen (z.B. Kleinanzeigen-Kontaktkasten)."""
    if not db.get(lid):
        raise HTTPException(404)
    return {"phones": pipeline.fetch_phone(lid)}


@app.post("/api/check/{lid:path}")
def check(lid: str):
    if not db.get(lid):
        raise HTTPException(404)
    return pipeline.check_listing(lid)


@app.get("/api/listings/{lid:path}")
def listing(lid: str):
    row = db.get(lid)
    if not row:
        raise HTTPException(404)
    return row


@app.patch("/api/listings/{lid:path}")
def edit(lid: str, data: dict = Body(...)):
    fields = {k: v for k, v in data.items() if k in USER_EDITABLE}
    if "user_gone" in fields:  # "nicht mehr online" gemeldet bzw. zurückgenommen
        fields["active"] = 0 if fields["user_gone"] else 1
    db.update(lid, **fields)
    return db.get(lid)


@app.post("/api/scrape")
def scrape(data: dict = Body(default={})):
    if pipeline.STATUS["running"]:
        return {"started": False, "reason": "läuft bereits"}
    threading.Thread(target=pipeline.run, args=(data.get("sources"),), daemon=True).start()
    return {"started": True}


@app.get("/api/status")
def status():
    last = db.last_run()
    return {**pipeline.STATUS, "last_run": last and {"started": last["started"], "finished": last["finished"]},
            "sources_available": {k: s.label for k, s in SCRAPERS.items()}, "config": load_config()}


@app.get("/api/match")
def match_scores():
    return match.compute()


@app.post("/api/reextract")
def reextract():
    return {"updated": pipeline.reextract_all()}


@app.get("/img")
def img(u: str):
    """Bild-Proxy mit Festplatten-Cache."""
    if not images.allowed(u):
        raise HTTPException(400, "Host nicht erlaubt")
    got = images.get(u)
    if not got:
        raise HTTPException(502)
    return Response(got[0], media_type=got[1], headers={"Cache-Control": "max-age=31536000"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
