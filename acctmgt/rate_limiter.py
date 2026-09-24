import time
import logging
from functools import wraps
from django.core.cache import cache
from django.contrib import messages
from django.shortcuts import redirect
from .utils import get_client_ip

logger = logging.getLogger(__name__)


def check_rate_limit(ip_address, action_key, max_requests, window_seconds):
    """
    Check if an IP address has exceeded the maximum number of requests in a time window.
    Uses Django's cache backend (Redis, Memcached, or Database/Local Memory cache).
    
    Args:
        ip_address (str): The client IP address.
        action_key (str): Identifier for the action/endpoint (e.g., 'forgot_password').
        max_requests (int): Maximum requests allowed within the window.
        window_seconds (int): Time window in seconds.
        
    Returns:
        tuple: (is_allowed: bool, retry_after_seconds: int)
    """
    if not ip_address:
        return True, 0

    cache_key = f"ratelimit:{action_key}:{ip_address}"
    now = time.time()
    cutoff = now - window_seconds

    # Fetch existing request timestamps
    timestamps = cache.get(cache_key, [])

    # Filter timestamps within current window
    valid_timestamps = [t for t in timestamps if t > cutoff]

    if len(valid_timestamps) >= max_requests:
        oldest = valid_timestamps[0]
        retry_after = max(1, int(window_seconds - (now - oldest)))
        logger.warning(
            f"[RATE LIMIT] IP {ip_address} exceeded limit for '{action_key}' "
            f"({len(valid_timestamps)}/{max_requests} in {window_seconds}s). Retry after {retry_after}s."
        )
        return False, retry_after

    # Record this request
    valid_timestamps.append(now)
    cache.set(cache_key, valid_timestamps, timeout=window_seconds)
    return True, 0


def check_request_rate_limit(request, action_key, max_requests, window_seconds):
    """
    Convenience wrapper to rate limit directly from an HttpRequest object.
    
    Returns:
        tuple: (is_allowed: bool, retry_after_seconds: int)
    """
    ip = get_client_ip(request) or '127.0.0.1'
    return check_rate_limit(ip, action_key, max_requests, window_seconds)
