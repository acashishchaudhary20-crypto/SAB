#!/bin/bash
# Double-click this file on a Mac to start the Southern Bypass Project system.
# (First time only: right-click it > Open > Open, because it was downloaded from the internet.)
cd "$(dirname "$0")"
echo "Checking required packages..."
python3 -m pip install -q -r requirements.txt
(sleep 3; open "http://127.0.0.1:8000") &
echo ""
echo "SAB is running at http://127.0.0.1:8000  -- keep this window open. Close it to stop SAB."
python3 run.py --port 8000
