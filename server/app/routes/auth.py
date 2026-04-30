from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel
import jwt
import os
import time
import logging
import httpx
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
router = APIRouter()

SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours

# ─────────────────────────────────────────────────────────────────────────────
# FIX C1: Read secrets lazily at request time, NOT at module import time.
# Previously these were read as module-level globals, before load_dotenv() ran
# in the lifespan, so they were always None — crashing every jwt.encode() call.
# ─────────────────────────────────────────────────────────────────────────────
def _get_session_secret() -> str:
    secret = os.getenv("SESSION_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Server misconfigured: SESSION_SECRET not set.")
    return secret

def _get_azure_client_id() -> Optional[str]:
    return os.getenv("AZURE_CLIENT_ID")

def _get_azure_tenant_id() -> Optional[str]:
    return os.getenv("AZURE_TENANT_ID")


# ─────────────────────────────────────────────────────────────────────────────
# FIX S3: In-memory JWKS cache with TTL (1 hour).
# Previously, every login hit login.microsoftonline.com live — slow, rate-limited,
# and a single point of failure. Microsoft's signing keys rotate infrequently
# (days/weeks), so caching for 1 hour is safe and dramatically more resilient.
# ─────────────────────────────────────────────────────────────────────────────
_jwks_cache: Dict[str, Any] = {}   # key: tenant_id → {"keys": [...], "fetched_at": float}
_JWKS_TTL_SECONDS = 3600           # 1 hour

async def _get_jwks(tenant_id: str) -> Dict:
    cached = _jwks_cache.get(tenant_id)
    if cached and (time.time() - cached["fetched_at"]) < _JWKS_TTL_SECONDS:
        return cached["data"]

    jwks_uri = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(jwks_uri)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch JWKS for tenant {tenant_id}: {e}")
        raise HTTPException(status_code=502, detail="Could not reach Microsoft to verify token.")

    _jwks_cache[tenant_id] = {"data": data, "fetched_at": time.time()}
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Request schema
# ─────────────────────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    id_token: str


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/auth/session
# Frontend sends the MSAL id_token here; we verify it and set an HttpOnly cookie
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/session")
async def create_session(body: TokenRequest, response: Response):
    """
    Exchange a valid Azure AD id_token for a server-side HttpOnly session cookie.
    The frontend (AuthGate) calls this once after MSAL login succeeds.
    """
    id_token = body.id_token
    session_secret = _get_session_secret()

    # --- 1. Decode WITHOUT verification first to extract kid/tenant ---
    try:
        unverified = jwt.decode(id_token, options={"verify_signature": False})
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed id_token.")

    # --- 2. Determine tenant and fetch (cached) JWKS ---
    tenant_id = _get_azure_tenant_id() or unverified.get("tid")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Cannot determine tenant from token.")

    jwks = await _get_jwks(tenant_id)

    # --- 3. Verify the token signature & claims ---
    # FIX C2: jwt.get_unverified_header() can raise jwt.DecodeError if the token
    # header is malformed — this was not caught before, producing an unhandled 500.
    # Now the entire key-lookup block is guarded against DecodeError and KeyError.
    try:
        token_header = jwt.get_unverified_header(id_token)
        token_kid = token_header.get("kid")
        if not token_kid:
            raise HTTPException(status_code=400, detail="Token header missing 'kid' field.")

        matching_key = next(
            (k for k in jwks.get("keys", []) if k.get("kid") == token_kid),
            None,
        )
        if matching_key is None:
            # Stale cache — force a refresh and try once more
            _jwks_cache.pop(tenant_id, None)
            jwks = await _get_jwks(tenant_id)
            matching_key = next(
                (k for k in jwks.get("keys", []) if k.get("kid") == token_kid),
                None,
            )
        if matching_key is None:
            raise HTTPException(status_code=401, detail="Signing key not found — token may be tampered.")

        signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(matching_key)
        client_id = _get_azure_client_id() or unverified.get("aud")
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=client_id,
            options={"verify_exp": True},
        )
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="id_token has expired.")
    except jwt.DecodeError as e:
        logger.warning(f"Malformed id_token header: {e}")
        raise HTTPException(status_code=400, detail="Malformed id_token.")
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid id_token: {e}")
        raise HTTPException(status_code=401, detail="Invalid id_token.")

    # --- 4. Extract user info from verified claims ---
    user_email = claims.get("preferred_username") or claims.get("email") or claims.get("upn")
    user_name = claims.get("name", "")
    if not user_email:
        raise HTTPException(status_code=400, detail="Token does not contain a user email.")

    logger.info(f"Session created for {user_email}")

    # --- 5. Mint our own short-lived session JWT (stored in HttpOnly cookie) ---
    now = int(time.time())
    session_payload = {
        "sub": user_email,
        "name": user_name,
        "iat": now,
        "exp": now + SESSION_MAX_AGE,
    }
    session_token = jwt.encode(session_payload, session_secret, algorithm="HS256")

    # --- 6. Set the HttpOnly cookie ---
    is_secure = os.getenv("APP_ENV") == "production"
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,          # JS cannot read this — XSS protection
        secure=is_secure,       # Only HTTPS in production; HTTP in dev
        samesite="lax",         # Protects against CSRF
        max_age=SESSION_MAX_AGE,
        path="/",
    )

    return {"message": "Session established.", "user": user_email, "name": user_name}


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/auth/logout
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/logout")
async def logout(response: Response):
    """Clear the server-side session cookie."""
    response.delete_cookie(key="session", path="/")
    return {"message": "Logged out successfully."}


# ─────────────────────────────────────────────────────────────────────────────
# Dependency: get_current_user
# Use Depends(get_current_user) on any protected route.
# Returns {"email": str, "name": str}
# ─────────────────────────────────────────────────────────────────────────────

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("session")

    if not token:
        raise HTTPException(status_code=401, detail="Missing session cookie. Please log in.")

    session_secret = _get_session_secret()

    try:
        payload = jwt.decode(token, session_secret, algorithms=["HS256"])
        user_email = payload.get("sub")
        if not user_email:
            raise HTTPException(status_code=401, detail="Invalid session data.")
        # Always return with the canonical "email" key so all downstream
        # code (verify_admin, check_admin_status, data routes) reads the same field.
        return {"email": user_email, "name": payload.get("name", "")}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token.")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/auth/me
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Returns the currently authenticated user. React can call this on page load."""
    return {"user": current_user}