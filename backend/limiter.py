from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from backend.config import RATELIMIT_STORAGE_URI

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=RATELIMIT_STORAGE_URI,
    default_limits=["200 per minute"],
    headers_enabled=True,
)
