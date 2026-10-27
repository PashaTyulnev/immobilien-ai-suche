# Wohnungssuche Dresden

Persönliches Tool: sammelt Mietwohnungen in Dresden von mehreren Portalen, extrahiert die Eckdaten
(Kosten, Fläche, Zimmer, Etage, Balkon, Baujahr …) und zeigt alles in einer filterbaren Übersicht mit Fotos.

## Start

Doppelklick auf `start.bat` → öffnet <http://localhost:8765>.
Beim ersten Start wird automatisch ein Suchlauf gestartet, danach alle 4 Stunden (solange der Server läuft).
„Jetzt suchen“ startet sofort einen Lauf.

## Quellen

| Quelle | Status |
|---|---|
| Immowelt (inkl. Immonet) | ✓ |
| Kleinanzeigen | ✓ |
| ohne-makler.net | ✓ (nur Dresdner PLZ) |
| WG-Gesucht (Wohnungen + 1-Zi.-Wohnungen) | ✓ langsam abgefragt; zeigt bei zu vielen Abrufen ein Captcha → Quelle wird dann für den Lauf übersprungen |
| immobilien.de | ✓ |
| dresdden.de (Johannes & Partner Immobilien) | ✓ eigene Mietangebote des Maklers, mit Telefonnummer |
| markt.de | ✓ Kleinanzeigen, nur Dresdner PLZ, ohne Gesuche/Gewerbe/Stellplätze |
| Makler: EdelWert, EXIT, VIAREALIS, Beate Protze, uniplan TerraCotta | ✓ eigene Webseiten, nur Mietobjekte in Dresden |
| WiD, SWG, WG Aufbau, EWG, WG Glückauf Süd, WG Johannstadt | ✓ eigene Webseiten (gemeinsamer Scraper `app/scrapers/coops.py`); Genossenschaften brauchen Anteile |
| ImmoScout24 | über Suchauftrag-Mails (IMAP), siehe unten – die Website selbst blockt Bots (HTTP 401) |
| Quoka | ✗ keine Städtesuche mehr, kaum Dresdner Angebote |

Erster Lauf dauert länger (jedes Exposé wird einmal geladen), danach werden nur neue Inserate nachgeladen.

**Online/offline:**
- Wurde die Ergebnisliste vollständig gelesen, gelten fehlende Inserate sofort als offline.
- Bei teilweise gelesenen Listen (z.B. Kleinanzeigen, max. 40 Seiten) gilt ein Inserat als offline, wenn es 3 Tage in keiner Liste auftauchte.
- Favoriten und Likes werden bei jedem Lauf gezielt auf ihrer Inseratsseite geprüft (gelöscht, umgeleitet, „Anzeige ist nicht mehr verfügbar“).
- In der Detailansicht prüft „Noch online?“ ein einzelnes Inserat sofort.
- ImmoScout: gelöschtes Foto = vergeben. Blockt ein Portal gerade (z.B. Immowelt), wird nichts als offline markiert.
- Offline-Favoriten bleiben unter ★ Favoriten sichtbar, mit „offline“-Hinweis.

### ImmoScout24 einrichten

1. Bei ImmoScout24 einen Suchauftrag (Mietwohnung Dresden, Benachrichtigung „sofort“) an deine Gmail-Adresse anlegen.
2. Im Google-Konto (2-Faktor-Anmeldung muss an sein) unter <https://myaccount.google.com/apppasswords>
   ein App-Passwort erstellen. Gmail-Einstellungen → „Weiterleitung und POP/IMAP“ → IMAP aktivieren.
3. `config.json` im Projektordner anlegen bzw. ergänzen:

```json
{ "sources": { "immoscout": { "enabled": true, "user": "du@gmail.com", "app_password": "xxxx xxxx xxxx xxxx" } } }
```

Die App liest dann bei jedem Lauf die ImmoScout-Mails der letzten 14 Tage (nur lesend) und übernimmt die Inserate.
Meldest du bei einem Anbieter zweimal „nicht mehr online“, bekommen seine übrigen Inserate das Abzeichen „⚠ oft schon vergeben“.
Inserate, deren Foto ImmoScout gelöscht hat oder deren Mail älter als 14 Tage ist (`days` in config.json), gelten als vergeben. Ein vorhandenes Foto beweist nicht, dass das Inserat noch online ist – dann hilft „Ist vergeben“ in der Detailansicht bzw. der Grund „nicht mehr online“ (Taste 0) beim Swipen; solche Meldungen bleiben offline und zählen nicht fürs Lernen.
`config.json` enthält damit ein Passwort – nicht weitergeben.

## Wie die Daten entstehen

1. **Strukturierte Daten** der Portale (z.B. Immowelt-JSON, Kleinanzeigen-Merkmalliste)
2. **Regex-Extraktion** aus dem Exposé-Text für fehlende Felder (`app/extract.py`)
3. **Lokales LLM** über Ollama (`qwen2.5:14b`) füllt restliche Lücken, bestimmt den Gebäudetyp
   und schreibt eine Kurzzusammenfassung (`app/llm.py`). Läuft im Hintergrund parallel zum Scraping.

## Filter

- **Vonovia & Töchter**: Wortliste (Vonovia, BUWOG, Deutsche Wohnen, Südost Woba, WOBA, GAGFAH) in Anbieter
  und Text – in der Seitenleiste editierbar.
- **Plattenbau-Score 0–100**: Baujahr 1960–1991, Geschosszahl, Stadtteil (Gorbitz, Prohlis, Reick, …),
  Stichworte (WBS 70, P2, Plattenbau …), KI-Gebäudetyp; Altbau/Neubau-Merkmale ziehen ab.
  Schwelle per Schieberegler; die Gründe stehen in der Detailansicht.
- Befristet/Zwischenmiete, möbliert, WBS-pflichtig, Erdgeschoss, eigene Ausschluss-Wörter.
- Duplikate (gleiche Wohnung auf mehreren Portalen, erkannt über Titel + Fläche/Preis bzw. exakte Fläche + Zimmer + Preis) werden zusammengefasst: beim Swipen kommt jede Wohnung nur einmal, und eine Ablehnung gilt für alle Portale.
- Favoriten, Ausblenden, Status (angefragt, Besichtigung …) und Notizen pro Wohnung.

## Swipen & Match

„♥ Swipen“ oben rechts zeigt eine Wohnung nach der anderen (nur die, die zu den aktuellen Filtern passen).
→ / ♥ = gefällt mir (landet in ★ Favoriten), ← / ✕ = nein (wird ausgeblendet), Leertaste/Tippen = nächstes Foto,
↑ = Details, Z = rückgängig. Auf dem Handy kann man die Karte wischen.

Aus den Bewertungen lernt `app/match.py` (logistische Regression über Miete, Fläche, Zimmer, Etage, Baujahr,
Plattenbau-Score, Ausstattung, Stadtteil, Gebäudetyp und Wörter aus Titel/Beschreibung) einen **Match-Wert 0–100**.
Ab dem ersten ♥ sucht es Ähnliche, ab 2 ♥ und 2 ✕ lernt es richtig.

Bei ✕ / ← / Wischen nach links fragt die App zuerst „Warum nicht?“ (über der Karte): Schnellgründe (Tasten 1–9),
Freitext (Taste T), Enter oder nochmal ← = ablehnen, Esc = abbrechen. Aus den Gründen werden Regeln:
„zu teuer“ → Abzug ab dieser Preisklasse, „zu klein/groß“ → Flächengrenze, „Lage“ → Stadtteil, „Erdgeschoss“,
„kein Balkon“, „Plattenbau/Block“, „befristet“, „Anbieter“; Freitext-Wörter (z.B. „Gasheizung“) werden in anderen
Inseraten gesucht. Jeder zutreffende Grund zieht 20 Punkte vom Match ab und steht als „✕ …“ auf der Karte. Die Swipe-Reihenfolge und die Sortierung
„Passt zu mir“ nutzen den Wert; jede 5. Karte ist bewusst zufällig, damit das Modell nicht nur Bekanntes bestätigt.

## Konfiguration

Optional `config.json` im Projektordner anlegen (überschreibt Defaults aus `app/config.py`), z.B.:

```json
{
  "auto_scrape_hours": 2,
  "llm": {"model": "llama3.1:8b"},
  "sources": {"wggesucht": {"enabled": false}}
}
```

Daten liegen in `data/wohnungen.db` (SQLite), Bilder werden in `data/img_cache/` zwischengespeichert.

## Telefonnummern

- Aus dem Inseratstext und (Kleinanzeigen) aus dem Kontaktkasten; Fax, PLZ, Preise und Objektnummern werden ignoriert.
- Anbieter-Telefonbuch, bei jedem Suchlauf aktualisiert: dresdden.de (Johannes & Partner) und immoboerse-dd.de (Antje Schmidt Immobilien – Zentrale und
  Ansprechpartner; wird im Inserat eine Ansprechperson genannt, steht deren Nummer zuerst).
- „☎ Nummer suchen“ lädt bei Bedarf die Inseratsseite neu (nicht bei Immowelt/ImmoScout, die blocken Abrufe).
- Auf dem Handy startet ein Tipp auf die Nummer direkt den Anruf.

## Makler-Crawler („🏢 Makler“)

Sammelt Dresdner Makler/Vermieter (gewerbliche Anbieter aus den Inseraten + Startliste mit WiD und
Wohnungsgenossenschaften), findet ihre Webseite (Inseratstext, immobilien.de-Maklerprofil oder Name geraten
und geprüft) und schaut auf Startseite + bis zu 12 Unterseiten, ob dort eigene Mietangebote stehen.
Ergebnis je Anbieter: eigene Mietangebote / Makler-Software (onOffice, FlowFact, Wohnungshelden …) /
nur Immowelt- bzw. ImmoScout-Modul (schon abgedeckt) / keine Angebote. Beachtet robots.txt, 1 s Abstand je Seite.
Seiten mit eigenen Angeboten sind Kandidaten für einen eigenen Scraper (wie dresdden.de).
