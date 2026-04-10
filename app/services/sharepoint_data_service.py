"""
SharePoint Data Service
Fetches and writes data to SharePoint Excel files using Microsoft Graph API
"""
import logging
import json
from typing import Dict, List, Any, Optional
from pathlib import Path
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import io

from .sharepoint_auth import SharePointAuthManager, load_auth_config_from_env

logger = logging.getLogger(__name__)

# Timezone for data processing
IST = ZoneInfo("Asia/Kolkata")

# Placeholder config - will be set from environment variables or config.yaml
SHAREPOINT_CONFIG = {
    
    "last_7_days": {
        "name": "last_7_days",
        "site_url": "",
        "drive_id": "",
        "file_name": "last_7_days.xlsx",
        "sheet_name": "Sheet1",
        "timestamp_field": "Date",
        "date_field": "Date",
        "numeric_columns": ['Start Value', 'End Value', 'Generation (Wh)']
    },
    "smb_status": {
        "name": "SMB_Status",
        "site_url": "",
        "drive_id": "",
        "file_name": "smb_status.xlsx",
        "sheet_name": "Sheet1",
        "timestamp_field": "Date",
        "date_field": "Date",
        "numeric_columns": ['SMB1', 'SMB2', 'SMB3', 'SMB4', 'SMB5']
    },
    "unified_solar": {
        "name": "UnifiedSolarData",
        "site_url": "https://testmaq.sharepoint.com/Admin",
        "drive_id": "b!pp-Tg7hGtEajA8Ko1ZlOH90SzEweDJZOhewh22hLObrahTJ2CauFToyUPFVjjP6F", # <--- Paste the same Drive ID you got from the script
        "folder_path": "22. Facilities Report/MIPL/Noida/2. Electrical data/",
        "file_name": "UnifiedSolarData.xlsx", # Make sure this matches the exact file name in SharePoint!
        "sheet_name": "Sheet1",
        "timestamp_field": "Time",
        "date_field": "Date",
        "numeric_columns": [
            'DC Power (kW)', 'AC Power (kW)', 'Current Total (A)',
            'Current Average (A)', 'Active Power (kW)', 'Apparent Power (kVA)',
            'Power Factor', 'Frequency (Hz)',
            'Voltage Phase-to-Phase (V)', 'Voltage Phase-to-Neutral (V)',
            'V1 (V)', 'V2 (V)', 'V3 (V)',
            'Day Generation (kWh)', 'Day Import (kWh)', 'Day Export (kWh)',
            'Total Import (kWh)', 'Total Export (kWh)',
            'DC Capacity (kWp)', 'AC Capacity (kW)'
        ]
    },
    "grid_and_diesel": {
        "name": "grid_and_diesel",
        "site_url": "https://testmaq.sharepoint.com/Admin",
        "drive_id": "b!pp-Tg7hGtEajA8Ko1ZlOH90SzEweDJZOhewh22hLObrahTJ2CauFToyUPFVjjP6F", # <--- Paste the same Drive ID you got from the script
        "folder_path": "22. Facilities Report/MIPL/Noida/2. Electrical data/",
        "file_name": "Electrical Optimization (1).xlsx", # Make sure this matches the exact file name in SharePoint!
        "sheet_name": "Sheet1",
        "timestamp_field": "Time",
        "date_field": "Date",
        "numeric_columns": [
            'Grid Units Consumed (KWh)', 'DG Units Consumed (KWh)',
            'Total Units Consumed in INR', 'Grid Cost (INR)',
            'Diesel Cost (INR)', 'Total Cost (INR)', 'Energy Saving (INR)'
        ]
    },
    "master_data": {
        "name": "master_data",
        "site_url": "https://testmaq.sharepoint.com/Admin",
        "drive_id": "b!pp-Tg7hGtEajA8Ko1ZlOH90SzEweDJZOhewh22hLObrahTJ2CauFToyUPFVjjP6F", # (We will get this in step 3!)
        "folder_path": "22. Facilities Report/MIPL/Noida/2. Electrical data/", # <--- Added from your old script
        "file_name": "Master-data.xlsx",
        "sheet_name": "Sheet1",
        "timestamp_field": "Time",
        "date_field": "Date",
        "numeric_columns": [
            'Grid Units Consumed (KWh)',
            'Solar Units Consumed(KWh)', 'Total Units Consumed (KWh)',
            'Total Units Consumed in INR', 'Energy Saving in INR',
            'Number of Panels Cleaned', 'Diesel consumed',
            'Water treated through STP', 'Water treated through WTP'
        ]
    }
}


class SharePointDataService:
    """Service to read/write data from/to SharePoint Excel files"""
    
    def __init__(self, auth_manager: Optional[SharePointAuthManager] = None):
        """
        Initialize the service with SharePoint authentication
        
        Args:
            auth_manager: SharePointAuthManager instance (creates new if None)
        """
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
        """Get the last error message"""
        return self.last_error
    def _get_file_item_id(self, site_url: str, drive_id: str, file_name: str, folder_path: str = "") -> Optional[str]:
        if not self.authenticated:
            self.last_error = "Not authenticated"
            return None
        
        try:
            headers = self.auth_manager.get_headers()
            
            # Format the folder path safely
            clean_path = folder_path.strip("/")
            if clean_path:
                clean_path += "/"
                
            item_path = f"{clean_path}{file_name}"
            
            # Use Graph API direct path lookup (root:/path/to/file.xlsx)
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
        """
        Fetch data from a SharePoint Excel sheet
        
        Args:
            sheet_key: Key of sheet configuration (e.g., 'unified_solar')
            
        Returns:
            DataFrame with sheet data or None
        """
        if not self.authenticated:
            self.last_error = "Not authenticated with SharePoint"
            logger.error(self.last_error)
            return None
        
        if sheet_key not in SHAREPOINT_CONFIG:
            self.last_error = f"Unknown sheet key: {sheet_key}"
            logger.error(self.last_error)
            return None
        
        config = SHAREPOINT_CONFIG[sheet_key]
        
        # Check if configuration is complete
        if not config.get("site_url") or not config.get("drive_id"):
            self.last_error = f"SharePoint configuration incomplete for {sheet_key}"
            logger.warning(f"{self.last_error}. Update config.yaml with site_url and drive_id")
            return None
        
        try:
            # Get file item ID
            file_item_id = self._get_file_item_id(config["site_url"], config["drive_id"], config["file_name"], config.get("folder_path", ""))
            if not file_item_id:
                self.last_error = f"Could not find file: {config['file_name']}"
                logger.error(self.last_error)
                return None
            
            # Download file content
            headers = self.auth_manager.get_headers()
            download_url = f"{self.graph_base_url}/drives/{config['drive_id']}/items/{file_item_id}/content"
            response = requests.get(download_url, headers=headers)
            
            if response.status_code != 200:
                self.last_error = f"Failed to download file: {response.status_code}"
                logger.error(f"{self.last_error}: {response.text}")
                return None
            
            # Read Excel file from bytes
            excel_file = io.BytesIO(response.content)
            df = pd.read_excel(excel_file, sheet_name=config["sheet_name"])
            
            logger.info(f"Successfully fetched data from SharePoint: {sheet_key} ({len(df)} rows)")
            return df
        
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Error fetching data from SharePoint: {e}")
            return None
    
    def get_data_by_date_range(
        self, sheet_key: str, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        Fetch data from SharePoint and filter by date range
        
        Args:
            sheet_key: Sheet key
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            
        Returns:
            Filtered DataFrame or None
        """
        df = self.fetch_sheet_data(sheet_key)
        if df is None:
            return None
        
        if sheet_key not in SHAREPOINT_CONFIG:
            return df
        
        config = SHAREPOINT_CONFIG[sheet_key]
        date_field = config.get("date_field", "Date")
        
        if date_field not in df.columns:
            return df
        
        try:
            df[date_field] = pd.to_datetime(df[date_field], errors='coerce')
            
            if start_date:
                start_dt = pd.to_datetime(start_date)
                df = df[df[date_field] >= start_dt]
            
            if end_date:
                end_dt = pd.to_datetime(end_date)
                df = df[df[date_field] <= end_dt]
            
            logger.info(f"Filtered {sheet_key} to date range: {start_date} to {end_date} ({len(df)} rows)")
            return df
        
        except Exception as e:
            logger.error(f"Error filtering by date range: {e}")
            return df
    
    def get_latest_rows(self, sheet_key: str, limit: int = 1) -> Optional[pd.DataFrame]:
        """
        Get the latest N rows from SharePoint
        
        Args:
            sheet_key: Sheet key
            limit: Number of rows to return
            
        Returns:
            DataFrame with latest rows or None
        """
        df = self.fetch_sheet_data(sheet_key)
        if df is None or df.empty:
            return None
        
        config = SHAREPOINT_CONFIG[sheet_key]
        timestamp_field = config.get("timestamp_field", "Timestamp")
        
        # Try to find timestamp column
        date_col = None
        if "Timestamp" in df.columns:
            date_col = "Timestamp"
        elif timestamp_field in df.columns:
            date_col = timestamp_field
        elif "Date" in df.columns:
            date_col = "Date"
        
        if date_col:
            try:
                df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
                df = df.sort_values(date_col, ascending=False)
            except Exception as e:
                logger.warning(f"Could not sort by {date_col}: {e}")
        
        return df.head(limit)


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


def reset_service():
    """Reset the singleton (for testing)"""
    global _sharepoint_service
    _sharepoint_service = None

