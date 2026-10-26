@echo off
rem Startet die Wohnungssuche und oeffnet die Oberflaeche im Browser.
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" http://localhost:8765
set PYTHONIOENCODING=utf-8
.venv\Scripts\python -m app.main
