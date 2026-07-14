"""
Shared one-time approval token store, backed by Redis. Used by both
api.py's /propose endpoint and events/consumer_orchestrator.py, so a
proposal from either path can be approved through the same POST
/confirm — the token schema and Redis key prefix have to stay
identical between them for that to work.
"""

import json
import os
import secrets

import redis

TOKEN_TTL_SECONDS = 1800  # 30 minutes/1800 min


def redis_client() -> redis.Redis:
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL is not set — required for the approval token store.")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def create_token(resource_name: str, thread_id: str, recommendation: dict) -> str:
    """Stores a one-time approval token in Redis and returns it."""
    token = secrets.token_urlsafe(32)
    payload = json.dumps({
        "thread_id": thread_id,
        "resource_name": resource_name,
        "recommendation": recommendation,
    })
    redis_client().setex(f"finops:token:{token}", TOKEN_TTL_SECONDS, payload)
    return token
