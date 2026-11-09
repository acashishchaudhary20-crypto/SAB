"""Start the Southern Bypass Project Management System.

    python run.py                 # http://127.0.0.1:5000
    python run.py --host 0.0.0.0  # share on the site-office LAN
"""
import argparse

from sab.app import create_app

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()
    create_app().run(host=a.host, port=a.port, debug=a.debug)
