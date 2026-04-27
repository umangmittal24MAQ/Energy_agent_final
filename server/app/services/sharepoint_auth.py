"""
SharePoint Authentication Module
Handles Microsoft authentication using MSAL and Microsoft Graph API
"""
import logging
from typing import Optional, Dict, Any
from pathlib import Path
import json
import os

logger = logging.getLogger(__name__)

try:
    from msal import PublicClientApplication, ConfidentialClientApplication
    HAS_MSAL = True
except ImportError:
    HAS_MSAL = False
    logger.warning("MSAL not installed. Install with: pip install msal")


class SharePointAuthConfig:
    """Configuration for SharePoint authentication"""
    
    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        app_id_uri: Optional[str] = None,
        authority_url: Optional[str] = None,
        cache_path: Optional[Path] = None,
    ):
        """
        Initialize authentication configuration
        
        Args:
            tenant_id: Azure AD Tenant ID
            client_id: Application (client) ID from Azure AD
            client_secret: Client secret (for Service Principal auth)
            app_id_uri: Application ID URI (e.g., "https://graph.microsoft.com")
            authority_url: Custom authority URL (defaults to Microsoft Cloud)
            cache_path: Path to token cache file
        """
        self.tenant_id = tenant_id or os.getenv("SHAREPOINT_TENANT_ID", "")
        self.client_id = client_id or os.getenv("SHAREPOINT_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("SHAREPOINT_CLIENT_SECRET", "")
        self.app_id_uri = app_id_uri or "https://graph.microsoft.com/.default"
        
        # Build authority URL
        if authority_url:
            self.authority_url = authority_url
        elif self.tenant_id:
            self.authority_url = f"https://login.microsoftonline.com/{self.tenant_id}"
        else:
            self.authority_url = "https://login.microsoftonline.com/common"
        
        # Token cache
        self.cache_path = cache_path or Path(__file__).parent.parent.parent / ".sharepoint_token_cache"
        
        self._validate()
    
    def _validate(self):
        """Validate that required credentials are provided"""
        if not self.client_id:
            logger.warning("SharePoint Client ID not configured. Set SHAREPOINT_CLIENT_ID env var or provide client_id")
        if not self.tenant_id:
            logger.warning("SharePoint Tenant ID not configured. Set SHAREPOINT_TENANT_ID env var or provide tenant_id")
    
    def is_configured(self) -> bool:
        """Check if minimal credentials are provided"""
        return bool(self.client_id and self.tenant_id)


class SharePointAuthManager:
    """Manages Microsoft authentication and token refresh"""
    
    def __init__(self, config: SharePointAuthConfig):
        """
        Initialize auth manager
        
        Args:
            config: SharePointAuthConfig instance
        """
        if not HAS_MSAL:
            logger.error("MSAL library not available")
            self.auth = None
            self.config = config
            return
        
        self.config = config
        self.auth = None
        self.access_token: Optional[str] = None
        self._initialize_msal()
    
    def _initialize_msal(self) -> bool:
        """Initialize MSAL application"""
        if not HAS_MSAL or not self.config.is_configured():
            return False
        
        try:
            # Use ClientApplication for Service Principal (client_secret) flow
            # Use PublicClientApplication for user credentials flow
            if self.config.client_secret:
                self.auth = ConfidentialClientApplication(
                    client_id=self.config.client_id,
                    authority=self.config.authority_url,
                    client_credential=self.config.client_secret,
                )
                logger.info("Initialized MSAL with Service Principal (client credentials)")
            else:
                # For interactive flows (will prompt user)
                self.auth = PublicClientApplication(
                    client_id=self.config.client_id,
                    authority=self.config.authority_url,
                )
                logger.info("Initialized MSAL with Public Client Application")
            
            return True
        except Exception as e:
            logger.error(f"Failed to initialize MSAL: {e}")
            self.auth = None
            return False
    
    def get_access_token(self) -> Optional[str]:
        """
        Get a valid access token (cached or refreshed)
        
        Returns:
            Access token string or None if authentication fails
        """
        if not self.auth or not self.config.is_configured():
            logger.error("MSAL not configured. Provide credentials.")
            return None
        
        try:
            # For Service Principal flow
            if self.config.client_secret:
                token_response = self.auth.acquire_token_for_client(
                    scopes=[self.config.app_id_uri]
                )
            else:
                # Try silent token acquisition first (from cache)
                token_response = self.auth.acquire_token_silent(
                    scopes=[self.config.app_id_uri],
                    account=None
                )
                
                if not token_response or "access_token" not in token_response:
                    # Token not in cache, would need interactive login
                    logger.warning("Token not in cache. Interactive authentication required (not supported in headless mode)")
                    return None
            
            if "access_token" in token_response:
                self.access_token = token_response["access_token"]
                logger.debug(f"Token acquired successfully, expires in {token_response.get('expires_in')} seconds")
                return self.access_token
            else:
                error = token_response.get("error_description", token_response.get("error", "Unknown error"))
                logger.error(f"Failed to acquire access token: {error}")
                return None
        
        except Exception as e:
            logger.error(f"Exception acquiring access token: {e}")
            return None
    
    def get_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers with Bearer token for Microsoft Graph API requests
        
        Returns:
            Dictionary with Authorization header
        """
        token = self.get_access_token()
        if not token:
            return {}
        
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }


def load_auth_config_from_env() -> SharePointAuthConfig:
    """Load SharePoint auth config from environment variables"""
    return SharePointAuthConfig(
        tenant_id=os.getenv("SHAREPOINT_TENANT_ID"),
        client_id=os.getenv("SHAREPOINT_CLIENT_ID"),
        client_secret=os.getenv("SHAREPOINT_CLIENT_SECRET"),
        app_id_uri=os.getenv("SHAREPOINT_APP_ID_URI", "https://graph.microsoft.com/.default"),
    )

