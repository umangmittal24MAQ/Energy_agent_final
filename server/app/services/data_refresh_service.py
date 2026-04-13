"""
data_refresh_service.py
=================================
Simplified refresh service for the new Excel-based architecture.
"""
import logging
from datetime import datetime
from typing import Dict, Any

from app.services.sharepoint_data_service import get_service as get_excel_service

logger = logging.getLogger(__name__)

class DataRefreshService:
    """Service to test/refresh the connection to the Master Data Excel file."""

    @staticmethod
    def refresh_all_data() -> Dict[str, Any]:
        """Validates that the latest Master Data can be fetched."""
        result = {
            'timestamp': datetime.now().isoformat(),
            'successful': [],
            'failed': [],
            'errors': {}
        }
        try:
            sp_service = get_excel_service()
            df = sp_service.fetch_sheet_data("master_data")
            
            if df is not None and not df.empty:
                result['successful'].append('master_data_connection')
                logger.info("Data refresh connection test completed successfully")
            else:
                result['failed'].append('master_data')
                result['errors']['master_data'] = 'File is empty or missing'
                logger.warning("Master data returned empty during refresh")
                
        except Exception as e:
            result['failed'].append('master_data')
            result['errors']['master_data'] = str(e)
            logger.error(f"Error in data refresh task: {e}")
            
        return result