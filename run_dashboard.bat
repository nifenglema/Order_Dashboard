@echo off
cd /d "%~dp0"
echo Starting ERP Dashboard Backend...
start "" http://127.0.0.1:8000/login
python -m uvicorn main:app --port 8000
pause