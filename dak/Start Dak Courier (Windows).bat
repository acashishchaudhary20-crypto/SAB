@echo off
rem Double-click this file on the office PC to start the Dak Courier.
rem To start it automatically: press Win+R, type shell:startup, and put a shortcut to this file there.
cd /d "%~dp0"
python -m pip install -q pypdf 2>nul
python dak_courier.py --status
echo.
python dak_courier.py --watch
pause
