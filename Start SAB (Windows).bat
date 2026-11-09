@echo off
REM Double-click to start the Southern Bypass Project system on Windows.
cd /d "%~dp0"
echo Checking required packages...
python -m pip install -q -r requirements.txt
start "" http://127.0.0.1:8000
echo SAB is running at http://127.0.0.1:8000 - keep this window open. Close it to stop SAB.
python run.py --port 8000
