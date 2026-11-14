#!/bin/bash
# Double-click this file on the Mac to start the Dak Courier.
# (First time only: right-click it > Open > Open, because it was downloaded from the internet.)
# To start it automatically: System Settings > General > Login Items > "+" > choose this file.
cd "$(dirname "$0")"
python3 -m pip install -q pypdf 2>/dev/null || echo "(pypdf could not be installed - PDFs will be judged by file name only)"
python3 dak_courier.py --status
echo ""
python3 dak_courier.py --watch
