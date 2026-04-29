"""
SharePoint Data Service
Fetches and writes data to SharePoint Excel files using Microsoft Graph API
"""
import logging
import io
import os
from typing import Dict, Optional
import pandas as pd
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from .sharepoint_auth import SharePointAuthManager, load_auth_config_from_env
from app.core.retry import retry_with_backoff


logger = logging.getLogger(__name__)

# Timezone for data processing
IST = ZoneInfo("Asia/Kolkata")


def _load_sharepoint_config_from_env() -> Dict:
    """
    Load SharePoint configuration from environment variables.
    
    Required environment variables (set in Azure App Settings):
    - SHAREPOINT_UNIFIED_SOLAR_DRIVE_ID
    - SHAREPOINT_UNIFIED_SOLAR_FOLDER_PATH
    - SHAREPOINT_UNIFIED_SOLAR_FILE_NAME
    - SHAREPOINT_GRID_DIESEL_DRIVE_ID
    - SHAREPOINT_GRID_DIESEL_FOLDER_PATH
    - SHAREPOINT_GRID_DIESEL_FILE_NAME
    - SHAREPOINT_MASTER_DATA_DRIVE_ID
    - SHAREPOINT_MASTER_DATA_FOLDER_PATH
    - SHAREPOINT_MASTER_DATA_FILE_NAME
    - SHAREPOINT_SITE_URL (from config.py, or set explicitly)
    
    Falls back to empty strings if not set—validation happens at runtime.
    """
    site_url = os.getenv("SHAREPOINT_SITE_URL", "").strip()
    
    return {
        "unified_solar": {
            "name": "UnifiedSolarData",
            "site_url": site_url,
            "drive_id": os.getenv("SHAREPOINT_UNIFIED_SOLAR_DRIVE_ID", "").strip(),
            "folder_path": os.getenv("SHAREPOINT_UNIFIED_SOLAR_FOLDER_PATH", "").strip(),
            "file_name": os.getenv("SHAREPOINT_UNIFIED_SOLAR_FILE_NAME", "").strip(),
            "sheet_name": "Sheet1",
            "date_field": "Date"
        },
        "grid_and_diesel": {
            "name": "grid_and_diesel",
            "site_url": site_url,
            "drive_id": os.getenv("SHAREPOINT_GRID_DIESEL_DRIVE_ID", "").strip(),
            "folder_path": os.getenv("SHAREPOINT_GRID_DIESEL_FOLDER_PATH", "").strip(),
            "file_name": os.getenv("SHAREPOINT_GRID_DIESEL_FILE_NAME", "").strip(),
            "sheet_name": "Sheet1",
            "date_field": "Date"
        },
        "master_data": {
            "name": "master_data",
            "site_url": site_url,
            "drive_id": os.getenv("SHAREPOINT_MASTER_DATA_DRIVE_ID", "").strip(),
            "folder_path": os.getenv("SHAREPOINT_MASTER_DATA_FOLDER_PATH", "").strip(),
            "file_name": os.getenv("SHAREPOINT_MASTER_DATA_FILE_NAME", "").strip(),
            "sheet_name": "Sheet1",
            "date_field": "Date"
        }
    }


# Load configuration from environment variables (NOT hardcoded)
SHAREPOINT_CONFIG = _load_sharepoint_config_from_env()


class SharePointDataService:
    """Service to read/write data from/to SharePoint Excel files"""
    
    def __init__(self, auth_manager: Optional[SharePointAuthManager] = None):
        if auth_manager:
            self.auth_manager = auth_manager
        else:
            config = load_auth_config_from_env()
            self.auth_manager = SharePointAuthManager(config)
        
        self.authenticated = self.auth_manager.get_access_token() is not None
        self.last_error = None
        self.graph_base_url = "https://graph.microsoft.com/v1.0"
        
        if not self.authenticated:
            logger.warning("SharePoint authentication failed. Service will not function until credentials are provided.")
        
        # Validate SharePoint configuration is complete
        self._validate_configuration()
    
    def _validate_configuration(self) -> None:
        """Validate that all required SharePoint configuration is set via environment variables."""
        required_env_vars = [
            "SHAREPOINT_SITE_URL",
            "SHAREPOINT_UNIFIED_SOLAR_DRIVE_ID",
            "SHAREPOINT_UNIFIED_SOLAR_FOLDER_PATH",
            "SHAREPOINT_UNIFIED_SOLAR_FILE_NAME",
            "SHAREPOINT_GRID_DIESEL_DRIVE_ID",
            "SHAREPOINT_GRID_DIESEL_FOLDER_PATH",
            "SHAREPOINT_GRID_DIESEL_FILE_NAME",
            "SHAREPOINT_MASTER_DATA_DRIVE_ID",
            "SHAREPOINT_MASTER_DATA_FOLDER_PATH",
            "SHAREPOINT_MASTER_DATA_FILE_NAME",
        ]
        
        missing_vars = [var for var in required_env_vars if not os.getenv(var, "").strip()]
        
        if missing_vars:
            logger.warning(
                f"⚠️  SharePoint configuration incomplete. Missing environment variables: {', '.join(missing_vars)}. "
                "Set these in Azure App Settings or .env file:\n"
                "  - SHAREPOINT_SITE_URL\n"
                "  - SHAREPOINT_UNIFIED_SOLAR_DRIVE_ID, FOLDER_PATH, FILE_NAME\n"
                "  - SHAREPOINT_GRID_DIESEL_DRIVE_ID, FOLDER_PATH, FILE_NAME\n"
                "  - SHAREPOINT_MASTER_DATA_DRIVE_ID, FOLDER_PATH, FILE_NAME"
            )
    
    def get_last_error(self) -> Optional[str]:
        return self.last_error

    def _normalize_sheet_headers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalizes headers for sheets where row 1 contains the real column names."""
        if df is None or df.empty:
            return df

        cols = [str(c) for c in df.columns]
        if any("Unnamed" in c for c in cols):
            for i, row in df.head(10).iterrows():
                if any("date" in str(v).lower() for v in row.values):
                    df.columns = [str(c).strip().replace("\n", " ") for c in row.values]
                    df = df.iloc[i + 1:].reset_index(drop=True)
                    break
        else:
            df.columns = [str(c).strip().replace("\n", " ") for c in df.columns]

        return df

    def _get_file_item_id(self, site_url: str, drive_id: str, file_name: str, folder_path: str = "") -> Optional[str]:
        if not self.authenticated:
            self.last_error = "Not authenticated"
            return None
        
        try:
            headers = self.auth_manager.get_headers()
            clean_path = folder_path.strip("/")
            if clean_path:
                clean_path += "/"
                
            item_path = f"{clean_path}{file_name}"
            search_url = f"{self.graph_base_url}/drives/{drive_id}/root:/{item_path}"
            
            response = requests.get(search_url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                return response.json().get("id")
            else:
                self.last_error = f"Failed to get file ID: {response.status_code}"
                logger.error(f"{self.last_error}: {response.text}")
            
            return None
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Error getting file item ID: {e}")
            return None

    def fetch_sheet_data(self, sheet_key: str) -> Optional[pd.DataFrame]:
        """
        Fetch data from a SharePoint Excel sheet.
        Config/auth problems return None immediately (no retry needed).
        Network errors are retried up to 3 times via _fetch_with_retry().
        """
        # --- Config & auth checks — no point retrying these ---
        if not self.authenticated:
            self.last_error = "Not authenticated with SharePoint"
            logger.error(self.last_error)
            return None
        
        if sheet_key not in SHAREPOINT_CONFIG:
            self.last_error = f"Unknown sheet key: {sheet_key}"
            logger.error(self.last_error)
            return None
        
        config = SHAREPOINT_CONFIG[sheet_key]
        
        if not config.get("site_url") or not config.get("drive_id"):
            self.last_error = f"SharePoint configuration incomplete for {sheet_key}"
            logger.warning(f"{self.last_error}. Update config with site_url and drive_id")
            return None

        # --- Hand off to the retryable inner method ---
        try:
            return self._fetch_with_retry(sheet_key, config)
        except requests.exceptions.RequestException as e:
            # All 3 retry attempts exhausted
            self.last_error = str(e)
            logger.error(f"All retries failed for {sheet_key}: {e}")
            return None
        except Exception as e:
            # Unexpected non-network error (e.g. bad Excel file)
            self.last_error = str(e)
            logger.error(f"Unexpected error fetching {sheet_key}: {e}")
            return None

    @retry_with_backoff(
        max_retries=3,
        initial_delay=2.0,
        backoff_factor=2.0,
        exceptions=(requests.exceptions.RequestException,)
    )
    def _fetch_with_retry(self, sheet_key: str, config: dict) -> Optional[pd.DataFrame]:
        """
        Inner fetch method — this is what gets retried on network errors.
        Retry timing: attempt 1 fails → wait 2s → attempt 2 fails → wait 4s →
                      attempt 3 fails → wait 8s → attempt 4 fails → give up.
        """
        file_item_id = self._get_file_item_id(
            config["site_url"],
            config["drive_id"],
            config["file_name"],
            config.get("folder_path", "")
        )
        if not file_item_id:
            self.last_error = f"Could not find file: {config['file_name']}"
            logger.error(self.last_error)
            return None

        headers = self.auth_manager.get_headers()
        download_url = f"{self.graph_base_url}/drives/{config['drive_id']}/items/{file_item_id}/content"

        response = requests.get(download_url, headers=headers, timeout=30)
        response.raise_for_status()  # Turns 429/503 into RequestException so retry triggers

        excel_file = io.BytesIO(response.content)
        df = pd.read_excel(excel_file, sheet_name=config["sheet_name"])
        df = self._normalize_sheet_headers(df)

        logger.info(f"Successfully fetched data from SharePoint: {sheet_key} ({len(df)} rows)")
        return df


# Singleton instance
_sharepoint_service: Optional[SharePointDataService] = None

def get_service() -> SharePointDataService:
    """Get or create the SharePoint data service singleton"""
    global _sharepoint_service
    
    if _sharepoint_service is None:
        config = load_auth_config_from_env()
        auth_manager = SharePointAuthManager(config)
        _sharepoint_service = SharePointDataService(auth_manager)
    
    return _sharepoint_service