"""
data_refresh_service.py  (FIXED)
=================================
Removed dead Google Sheets imports that crash on startup.
All cache refresh now reads from SharePoint Lists and SuryaLogix API,
which is the correct post-migration data path.
"""
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import pandas as pd

from .cache_service import get_cache
from .ingestion_bridge import run_ingestion_once, get_loader_processor

loader, processor = get_loader_processor()
logger = logging.getLogger(__name__)


class DataRefreshService:
    """Service to refresh data from SharePoint + SuryaLogix sources."""

    @staticmethod
    def refresh_ingestion_data() -> Dict[str, Any]:
        """Run one Ingestion-agent pipeline cycle (scraper → SharePoint write)."""
        result = {
            'timestamp': datetime.now().isoformat(),
            'successful': [],
            'failed': [],
            'errors': {}
        }
        try:
            success = run_ingestion_once()
            if success:
                result['successful'].append('ingestion_pipeline')
                logger.info("Ingestion pipeline refresh completed successfully")
            else:
                result['failed'].append('ingestion_pipeline')
                result['errors']['ingestion_pipeline'] = 'Pipeline reported failures'
                logger.warning("Ingestion pipeline refresh completed with failures")
        except (ImportError, ModuleNotFoundError) as exc:
            result['failed'].append('ingestion_pipeline')
            result['errors']['ingestion_pipeline'] = f"Module load error: {str(exc)}"
            logger.error(f"Ingestion pipeline import error: {exc}")
        except Exception as exc:
            result['failed'].append('ingestion_pipeline')
            result['errors']['ingestion_pipeline'] = str(exc)
            logger.exception(f"Unexpected error in ingestion pipeline: {exc}")
        return result

    @staticmethod
    def refresh_all_data() -> Dict[str, Any]:
        """
        Refresh all data sources:
          1. Run the scraper ingestion pipeline (writes to Unified_Solar_List)
          2. Pull fresh data from SharePoint Lists into the in-memory cache
        """
        results = {
            'timestamp': datetime.now().isoformat(),
            'successful': [],
            'failed': [],
            'errors': {}
        }

        # Step 1: run scraper pipeline
        ingestion_result = DataRefreshService.refresh_ingestion_data()
        results['successful'].extend(ingestion_result['successful'])
        results['failed'].extend(ingestion_result['failed'])
        results['errors'].update(ingestion_result['errors'])

        # Step 2: refresh cache from SharePoint
        cache = get_cache()

        try:
            from .sharepoint_list_data_service import SharePointListDataService
            sp_service = SharePointListDataService()

            if not sp_service.is_authenticated():
                logger.warning("SharePoint not authenticated — skipping cache refresh")
                results['errors']['sharepoint_auth'] = sp_service.get_last_error() or "Not authenticated"
                return results

            # Solar / unified data
            try:
                solar_df = sp_service.get_solar_data()
                if solar_df is not None and not solar_df.empty:
                    cache.set('unified_solar', solar_df, ttl_seconds=600)
                    results['successful'].append('unified_solar')
                    logger.info(f"Refreshed unified_solar cache ({len(solar_df)} rows)")
                else:
                    results['failed'].append('unified_solar')
                    results['errors']['unified_solar'] = 'No data returned from Unified_Solar_List'
            except Exception as exc:
                results['failed'].append('unified_solar')
                results['errors']['unified_solar'] = str(exc)
                logger.error(f"Error refreshing unified_solar: {exc}")

            # Grid data
            try:
                grid_df = sp_service.get_grid_data()
                if grid_df is not None and not grid_df.empty:
                    cache.set('grid_data', grid_df, ttl_seconds=600)
                    results['successful'].append('grid_data')
                    logger.info(f"Refreshed grid_data cache ({len(grid_df)} rows)")
                else:
                    results['failed'].append('grid_data')
                    results['errors']['grid_data'] = 'No data returned from Grid_Diesel_List'
            except Exception as exc:
                results['failed'].append('grid_data')
                results['errors']['grid_data'] = str(exc)
                logger.error(f"Error refreshing grid_data: {exc}")

            # Master data (for email/report)
            try:
                master_df = sp_service.get_master_data()
                if master_df is not None and not master_df.empty:
                    cache.set('master_data', master_df, ttl_seconds=600)
                    results['successful'].append('master_data')
                    logger.info(f"Refreshed master_data cache ({len(master_df)} rows)")
                else:
                    results['failed'].append('master_data')
                    results['errors']['master_data'] = 'No data returned from Master_Data_List'
            except Exception as exc:
                results['failed'].append('master_data')
                results['errors']['master_data'] = str(exc)
                logger.error(f"Error refreshing master_data: {exc}")

        except Exception as exc:
            logger.error(f"Unexpected error in cache refresh: {exc}", exc_info=True)
            results['errors']['general'] = str(exc)

        total = len(results['successful']) + len(results['failed'])
        logger.info(f"Data refresh complete: {len(results['successful'])}/{total} sources updated")
        return results

    @staticmethod
    def get_unified_data_with_fallback(
        config: Dict[str, Any],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get unified data from cache or fall back to SharePoint live read."""
        cache = get_cache()
        try:
            cached_data = cache.get('unified_solar', for_frontend=True)
            if cached_data:
                df = pd.DataFrame(cached_data) if isinstance(cached_data, list) else cached_data
                logger.info(f"Using cached unified_solar data ({len(df)} rows)")
            else:
                logger.info("Cache miss — reading unified_solar directly from SharePoint")
                from .sharepoint_list_data_service import SharePointListDataService
                sp = SharePointListDataService()
                df = sp.get_solar_data()
                if df is None:
                    df = pd.DataFrame()

            if (start_date or end_date) and not df.empty:
                df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
                if start_date:
                    df = df[df['Date'] >= pd.to_datetime(start_date)]
                if end_date:
                    df = df[df['Date'] <= pd.to_datetime(end_date)]
                df['Date'] = df['Date'].dt.strftime('%Y-%m-%d')

            df = df.drop(columns=["Irradiance (W/m²)", "DG Runtime (hrs)", "Source", "source"], errors="ignore")
            data = df.to_dict('records')
            dates = pd.to_datetime(df.get('Date', pd.Series()), errors='coerce').dropna()
            return {
                "data": data,
                "date_range": {
                    "min_date": dates.min().strftime('%Y-%m-%d') if len(dates) > 0 else None,
                    "max_date": dates.max().strftime('%Y-%m-%d') if len(dates) > 0 else None,
                },
                "total_records": len(data),
            }
        except Exception as exc:
            logger.error(f"Error in get_unified_data_with_fallback: {exc}", exc_info=True)
            return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}

    @staticmethod
    def get_solar_data_with_fallback(
        config: Dict[str, Any],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get solar data from cache or SharePoint fallback."""
        cache = get_cache()
        try:
            cached_data = cache.get('unified_solar', for_frontend=True)
            if cached_data:
                df = pd.DataFrame(cached_data) if isinstance(cached_data, list) else cached_data
            else:
                from .sharepoint_list_data_service import SharePointListDataService
                sp = SharePointListDataService()
                df = sp.get_solar_data()
                if df is None:
                    df = pd.DataFrame()

            if (start_date or end_date) and not df.empty:
                df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
                if start_date:
                    df = df[df['Date'] >= pd.to_datetime(start_date)]
                if end_date:
                    df = df[df['Date'] <= pd.to_datetime(end_date)]
                df['Date'] = df['Date'].dt.strftime('%Y-%m-%d')

            data = df.to_dict('records')
            dates = pd.to_datetime(df.get('Date', pd.Series()), errors='coerce').dropna()
            return {
                "data": data,
                "date_range": {
                    "min_date": dates.min().strftime('%Y-%m-%d') if len(dates) > 0 else None,
                    "max_date": dates.max().strftime('%Y-%m-%d') if len(dates) > 0 else None,
                },
                "total_records": len(data),
            }
        except Exception as exc:
            logger.error(f"Error in get_solar_data_with_fallback: {exc}", exc_info=True)
            return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}