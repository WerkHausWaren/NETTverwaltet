"""
WSGI-Einstiegspunkt für den Produktionsbetrieb (z. B. Gunicorn):

    gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:app

Lokal wird weiterhin run.py verwendet (öffnet den Browser).
"""
from app import create_app

app = create_app()
