@echo off
REM Starts the Web Scrapper tool at http://127.0.0.1:8000
cd /d "%~dp0"
call .venv\Scripts\activate
python -m webapp
