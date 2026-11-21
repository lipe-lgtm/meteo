"""Vstupní bod pro gunicorn:  gunicorn wsgi:app

Běží za Apache (reverse proxy), takže skutečnou IP klienta bereme z X-Forwarded-For
(věříme právě jedné proxy před námi).
"""
from werkzeug.middleware.proxy_fix import ProxyFix

from meteo import create_app

app = create_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
