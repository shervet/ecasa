@echo off
rem Lance MeCasa depuis le dossier du projet (Windows).
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m mecasa.main
) else (
    python -m mecasa.main
)

if errorlevel 1 pause
