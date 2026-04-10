"""
SharePoint Configuration Loader
Loads SharePoint settings from config.yaml and environment variables
Provides configuration for dual Google Sheets + SharePoint setup
"""
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
import os

logger = logging.getLogger(__name__)

# Cache for loaded configuration
_sharepoint_config_cache: Optional[Dict[str, Any]] = None


def load_sharepoint_config() -> Dict[str, Any]:
    """
    Load SharePoint configuration from config.yaml
    
    Returns:
        Dictionary with SharePoint settings
    """
    global _sharepoint_config_cache
    
    if _sharepoint_config_cache is not None:
        return _sharepoint_config_cache
    
    try:
        config_file = Path(__file__).parent.parent / "config.yaml"
        
        if not config_file.exists():
            logger.warning(f"Config file not found: {config_file}")
            return _get_default_config()
        
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f) or {}
        
        sharepoint_config = config.get("sharepoint", {})
        _sharepoint_config_cache = sharepoint_config
        
        logger.info(f"Loaded SharePoint config (enabled: {sharepoint_config.get('enabled', False)})")
        return sharepoint_config
    
    except Exception as e:
        logger.error(f"Failed to load SharePoint config: {e}")
        return _get_default_config()


def _get_default_config() -> Dict[str, Any]:
    """Get default SharePoint configuration"""
    return {
        "enabled": False,
        "mode": "dual",
        "unified_solar": {"site_url": "", "drive_id": "", "file_name": "unified_solar.xlsx"},
        "last_7_days": {"site_url": "", "drive_id": "", "file_name": "last_7_days.xlsx"},
        "smb_status": {"site_url": "", "drive_id": "", "file_name": "smb_status.xlsx"},
        "grid_and_diesel": {"site_url": "", "drive_id": "", "file_name": "grid_and_diesel.xlsx"},
        "master_data": {"site_url": "", "drive_id": "", "file_name": "master_data.xlsx"},
    }


def is_sharepoint_enabled() -> bool:
    """Check if SharePoint integration is enabled"""
    config = load_sharepoint_config()
    return config.get("enabled", False)


def get_sharepoint_mode() -> str:
    """
    Get SharePoint operation mode
    
    Returns:
        "google" (Google Sheets only), "sharepoint" (SharePoint only), or "dual" (both)
    """
    config = load_sharepoint_config()
    return config.get("mode", "dual")


def get_sheet_config(sheet_key: str) -> Dict[str, str]:
    """
    Get configuration for a specific sheet
    
    Args:
        sheet_key: Sheet identifier (e.g., 'unified_solar')
        
    Returns:
        Dictionary with site_url, drive_id, file_name
    """
    config = load_sharepoint_config()
    sheet_config = config.get(sheet_key, {})
    
    # Allow environment variables to override config file
    site_url = os.getenv(f"SHAREPOINT_{sheet_key.upper()}_SITE_URL") or sheet_config.get("site_url", "")
    drive_id = os.getenv(f"SHAREPOINT_{sheet_key.upper()}_DRIVE_ID") or sheet_config.get("drive_id", "")
    
    return {
        "site_url": site_url,
        "drive_id": drive_id,
        "file_name": sheet_config.get("file_name", f"{sheet_key}.xlsx"),
    }


def reset_config_cache():
    """Reset configuration cache (for testing)"""
    global _sharepoint_config_cache
    _sharepoint_config_cache = None

