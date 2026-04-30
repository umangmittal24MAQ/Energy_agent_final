"""
Rate limiting configuration using slowapi + Redis
Protects authentication and critical endpoints from brute force and DoS attacks.
"""
import os
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Rate Limiter Initialization
# ──────────────────────────────────────────────────────────────────────────────
# Use Redis for multi-instance deployments (Azure App Service scaling)
# Fall back to in-memory storage for development/single-instance deployments

redis_url = os.getenv("REDIS_URL")

if redis_url:
    try:
        limiter = Limiter(
            key_func=get_remote_address,
            storage_uri=redis_url,
            default_limits=["200/hour"],  # Global default for undecorated routes
        )
        logger.info(f"✅ Rate limiter initialized with Redis backend: {redis_url[:30]}...")
    except Exception as e:
        logger.warning(f"Failed to initialize Redis rate limiter: {e}. Falling back to in-memory.")
        limiter = Limiter(
            key_func=get_remote_address,
            default_limits=["200/hour"],
        )
else:
    # Development/testing: in-memory limiter
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=["200/hour"],
    )
    logger.info("Rate limiter initialized with in-memory storage (development mode)")


# ──────────────────────────────────────────────────────────────────────────────
# Custom Error Handler
# ──────────────────────────────────────────────────────────────────────────────

@limiter.error_handler
async def rate_limit_error_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Handle rate limit exceeded errors with proper HTTP 429 response.
    
    Returns:
        JSONResponse with 429 status code and Retry-After header
    """
    retry_after = getattr(exc, "retry_after", 60)
    
    logger.warning(
        f"Rate limit exceeded for {get_remote_address(request)} on {request.url.path}. "
        f"Retry after {retry_after} seconds."
    )
    
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": "Too many requests. Please try again later.",
            "retry_after": int(retry_after),
        },
        headers={
            "Retry-After": str(int(retry_after)),
            "X-RateLimit-Limit": exc.pydantic_error_model.limit or "unknown",
        }
    )
