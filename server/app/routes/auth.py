from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel
import jwt
import os
import time
import logging
import httpx

logger = logging.getLogger(__name__)
router = APIRouter()

AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
SESSION_SECRET = os.getenv("SESSION_SECRET", "super-secret-key-change-in-prod")
SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours


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

    # --- 1. Decode WITHOUT verification first to extract kid/tenant ---
    try:
        unverified = jwt.decode(id_token, options={"verify_signature": False})
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed id_token.")

    # --- 2. Fetch Microsoft's public signing keys ---
    tenant_id = AZURE_TENANT_ID or unverified.get("tid")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Cannot determine tenant from token.")

    jwks_uri = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            jwks_response = await client.get(jwks_uri)
            jwks_response.raise_for_status()
            jwks = jwks_response.json()
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        raise HTTPException(status_code=502, detail="Could not reach Microsoft to verify token.")

    # --- 3. Verify the token signature & claims ---
    try:
        signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(
            next(
                k for k in jwks["keys"]
                if k["kid"] == jwt.get_unverified_header(id_token)["kid"]
            )
        )
        client_id = AZURE_CLIENT_ID or unverified.get("aud")
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=client_id,
            options={"verify_exp": True},
        )
    except StopIteration:
        raise HTTPException(status_code=401, detail="Signing key not found — token may be tampered.")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="id_token has expired.")
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
    session_payload = {
        "sub": user_email,
        "name": user_name,
        "iat": int(time.time()),
        "exp": int(time.time()) + SESSION_MAX_AGE,
    }
    session_token = jwt.encode(session_payload, SESSION_SECRET, algorithm="HS256")

    # --- 6. Set the HttpOnly cookie ---
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,          # JS cannot read this — XSS protection
        secure=False,           # Set to True in production (HTTPS only)
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
# Use Depends(get_current_user) on any protected route
# ─────────────────────────────────────────────────────────────────────────────

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("session")

    if not token:
        raise HTTPException(status_code=401, detail="Missing session cookie. Please log in.")

    try:
        payload = jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
        user_email = payload.get("sub")
        if not user_email:
            raise HTTPException(status_code=401, detail="Invalid session data.")
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