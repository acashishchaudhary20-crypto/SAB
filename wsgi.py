"""WSGI entry point for production servers, e.g.  waitress-serve --port=8000 wsgi:app"""
from sab.app import create_app

app = create_app()
