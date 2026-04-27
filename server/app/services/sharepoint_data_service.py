"""
SharePoint Data Service
Fetches and writes data to SharePoint Excel files using Microsoft Graph API
"""
import logging
import io
from typing import Dict, Optional
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

from .sharepoint_auth import SharePointAuthManager, load_auth_config_from_env

logger = logging.getLogger(__name__)

# Timezone for data processing
IST = ZoneInfo("Asia/Kolkata")

# Clean, unified configuration for our 3 core files
SHAREPOINT_CONFIG = {
    "unified_solar": {
        "name": "UnifiedSolarData",
        "site_url": "https://testmaq.sharepoint.com/Admin",
        "drive_id": "b!pp-Tg7hGtEajA8Ko1ZlOH90SzEweDJZOhewh22hLObrahTJ2CauFToyUPFVjjP6F",
        "folder_path": "22. Facilities Report/MIPL/Noida/2. Electrical data/",
        "file_name": "UnifiedSolarData.xlsx",
        "sheet_name": "Sheet1",
        "date_field": "Date"
    },
    "grid_and_diesel": {
        "name": "grid_and_diesel",
        "site_url": "https://testmaq.sharepoint.com/Admin",
        "drive_id": "b!pp-Tg7hGtEajA8Ko1ZlOH90SzEweDJZOhewh22hLObrahTJ2CauFToyUPFVjjP6F",
        "folder_path": "22. Facilities Report/MIPL/Noida/2. Electrical data/",
        "file_name": "Electrical Optimization (1).xlsx",
        "sheet_name": "Sheet1",
        "date_field": "Date"
    },
    "master_data": {
        "name": "master_data",
        "site_url": "https://testmaq.sharepoint.com/Admin",
        "drive_id": "b!pp-Tg7hGtEajA8Ko1ZlOH90SzEweDJZOhewh22hLObrahTJ2CauFToyUPFVjjP6F", 
        "folder_path": "22. Facilities Report/MIPL/Noida/2. Electrical data/",
        "file_name": "Master-data.xlsx",
        "sheet_name": "Sheet1",
        "date_field": "Date"
    }
}


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
                    df = df.iloc[i + 1 :].reset_index(drop=True)
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
            
            response = requests.get(search_url, headers=headers)
            
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
        """Fetch data from a SharePoint Excel sheet"""
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
        
        try:
            file_item_id = self._get_file_item_id(config["site_url"], config["drive_id"], config["file_name"], config.get("folder_path", ""))
            if not file_item_id:
                self.last_error = f"Could not find file: {config['file_name']}"
                logger.error(self.last_error)
                return None
            
            headers = self.auth_manager.get_headers()
            download_url = f"{self.graph_base_url}/drives/{config['drive_id']}/items/{file_item_id}/content"
            response = requests.get(download_url, headers=headers)
            
            if response.status_code != 200:
                self.last_error = f"Failed to download file: {response.status_code}"
                logger.error(f"{self.last_error}: {response.text}")
                return None
            
            excel_file = io.BytesIO(response.content)
            df = pd.read_excel(excel_file, sheet_name=config["sheet_name"])
            df = self._normalize_sheet_headers(df)
            
            logger.info(f"Successfully fetched data from SharePoint: {sheet_key} ({len(df)} rows)")
            return df
        
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Error fetching data from SharePoint: {e}")
            return None


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