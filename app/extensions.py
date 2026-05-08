"""Singletons that need to be importable from anywhere without circular deps."""

from apscheduler.schedulers.background import BackgroundScheduler
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
scheduler = BackgroundScheduler(timezone="UTC")
