from redis import Redis
from urllib.parse import urlparse
import os

def get_redis_connection():
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    parsed_url = urlparse(redis_url)
    return Redis(
        host=parsed_url.hostname,
        port=parsed_url.port,
        username=parsed_url.username,
        password=parsed_url.password,
        ssl=True,
        ssl_cert_reqs=None  # Disable verification for Heroku Redis
    )