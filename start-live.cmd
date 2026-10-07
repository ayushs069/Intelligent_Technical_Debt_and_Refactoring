@echo off
cd /d "%~dp0"
echo Starting the live analysis dashboard at http://127.0.0.1:8600
python run_live.py
pause
