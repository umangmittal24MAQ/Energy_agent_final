import logging
import httpx
from fastapi import APIRouter, Query, HTTPException
import os

from app.services.sharepoint_auth import SharePointAuthManager, load_auth_config_from_env

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_graph_token() -> str:
    import msal
    client_id     = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")
    tenant_id     = os.getenv("AZURE_TENANT_ID")

    if not all([client_id, client_secret, tenant_id]):
        raise HTTPException(status_code=503, detail="Azure AD credentials not configured.")

    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise HTTPException(status_code=503, detail="Could not acquire Graph token.")
    return result["access_token"]

@router.get("/people/search")
async def search_people(q: str = Query(..., min_length=1)):
    q = q.strip()
    if len(q) < 2:
        return {"results": []}

    try:
        token = _get_graph_token()

        params = {
            "$filter": f"startswith(displayName,'{q}') or startswith(mail,'{q}')",
            "$select": "displayName,mail,jobTitle,department",
            "$top": "10",
            "$count": "true",
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/users",
                headers=headers,
                params=params
            )

        if resp.status_code == 403:
            logger.error("Graph /users 403 — ensure User.Read.All (Application) is granted.")
            return {"results": [], "error": "Permission denied."}

        if not resp.is_success:
            logger.error(f"Graph error {resp.status_code}: {resp.text}")
            return {"results": [], "error": "Graph API error."}

        data = resp.json()
        results = [
            {
                "name": u.get("displayName", ""),
                "email": u.get("mail", ""),
                "jobTitle": u.get("jobTitle", ""),
                "department": u.get("department", ""),
            }
            for u in data.get("value", [])
            if u.get("mail") and "@" in u.get("mail", "")
        ]
        return {"results": results}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"People search failed: {e}", exc_info=True)
        return {"results": [], "error": "Search unavailable."}