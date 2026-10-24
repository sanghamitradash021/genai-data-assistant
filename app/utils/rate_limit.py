"""Shared slowapi Limiter instance: one object, wired into app.main and used as a decorator on routes."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
