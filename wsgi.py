"""Gunicorn entry point:  gunicorn -w 3 -b 127.0.0.1:5055 wsgi:app"""
from app import create_app

app = create_app()
