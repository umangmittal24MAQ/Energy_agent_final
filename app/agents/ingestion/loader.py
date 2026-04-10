"""
Data Loader Module
Handles loading data from multiple sources with fallback chains:
- Solar: SuryaLogix API → SharePoint Lists → Legacy JSON files
- Grid: SharePoint Lists → Legacy JSON files
- Diesel: SharePoint Lists → Legacy JSON files
"""
import json
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, date
import logging

from app.services.surya_logix_api_service import SuryaLogixAPIService
from app.services.sharepoint_list_data_service import SharePointListDataService

logger = logging.getLogger(__name__)

# Get the directory where the Ingestion-agent scripts are
INGESTION_AGENT_DIR = Path(__file__).parent

# Global service instances (lazy-loaded)
_surya_logix_service = None
_sharepoint_service = None


def _get_surya_logix_service() -> Optional[SuryaLogixAPIService]:
    """Get or create SuryaLogix service instance"""
    global _surya_logix_service
    if _surya_logix_service is None:
        try:
            _surya_logix_service = SuryaLogixAPIService()
            logger.info("SuryaLogix service initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize SuryaLogix service: {e}")
            _surya_logix_service = None
    return _surya_logix_service


def _get_sharepoint_service() -> Optional[SharePointListDataService]:
    """Get or create SharePoint service instance"""
    global _sharepoint_service
    if _sharepoint_service is None:
        try:
            _sharepoint_service = SharePointListDataService()
            logger.info("SharePoint service initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize SharePoint service: {e}")
            _sharepoint_service = None
    return _sharepoint_service


def load_json_file(filename: str) -> Dict[str, Any]:
    """Load a JSON file from the Ingestion-agent directory"""
    filepath = INGESTION_AGENT_DIR / filename

    if not filepath.exists():
        logger.warning(f"File not found: {filepath}")
        return {}

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {filename}: {e}")
        return {}


def load_current_dashboard_data() -> Dict[str, Any]:
    """Load current dashboard data (latest measurements)"""
    return load_json_file('filtered_dashboard_data.json')


def load_7day_data() -> Dict[str, Any]:
    """Load 7-day historical data"""
    return load_json_file('7day_final.json')


def load_smb_data() -> Dict[str, Any]:
    """Load SMB (Solar Box Unit) status data"""
    return load_json_file('smb_data_grid.json')


def load_solar_data(config: Any = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """
    Load solar data with fallback chain: SuryaLogix API → SharePoint Lists → Legacy JSON files

    Args:
        config: Configuration object (unused for now)
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)

    Returns:
        DataFrame with solar data
    """
    logger.info("Loading solar data with fallback chain")

    # Convert string dates to date objects
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = date.fromisoformat(start_date)
        except ValueError:
            logger.warning(f"Invalid start_date format: {start_date}")
    if end_date:
        try:
            end_dt = date.fromisoformat(end_date)
        except ValueError:
            logger.warning(f"Invalid end_date format: {end_date}")

    # Try SuryaLogix API first
    surya_service = _get_surya_logix_service()
    if surya_service:
        try:
            logger.info("Attempting to load solar data from SuryaLogix API")
            if start_date and end_date:
                df = surya_service.get_historical_data(start_date, end_date)
            else:
                # Get last 7 days if no date range specified
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                df = surya_service.get_historical_data(start_date, end_date)

            if not df.empty:
                logger.info(f"Successfully loaded {len(df)} solar records from SuryaLogix API")
                return df
            else:
                logger.warning("SuryaLogix API returned empty data")
        except Exception as e:
            logger.warning(f"SuryaLogix API failed: {e}")

    # Try SharePoint lists as secondary source
    sharepoint_service = _get_sharepoint_service()
    if sharepoint_service:
        try:
            logger.info("Attempting to load solar data from SharePoint lists")
            df = sharepoint_service.get_solar_data(start_dt, end_dt)
            if df is not None and not df.empty:
                logger.info(f"Successfully loaded {len(df)} solar records from SharePoint")
                return df
            else:
                logger.warning("SharePoint lists returned empty data")
        except Exception as e:
            logger.warning(f"SharePoint lists failed: {e}")

    # Fallback to legacy JSON files
    logger.info("Falling back to legacy JSON files for solar data")
    try:
        data = load_7day_data()
        records = []

        for entry in data.get('data', []):
            date_str = entry.get('Date', '')
            if not date_str:
                continue

            # Apply date filtering if provided
            if start_date and date_str < start_date:
                continue
            if end_date and date_str > end_date:
                continue

            records.append({
                'Date': date_str,
                'Day Generation (kWh)': entry.get('Day_Generation_kWh', 0),
                'Total Generation (kWh)': entry.get('Total_Generation_kWh', 0),
            })

        df = pd.DataFrame(records)
        logger.info(f"Loaded {len(df)} solar records from legacy JSON files")
        return df

    except Exception as e:
        logger.error(f"All solar data sources failed: {e}")
        return pd.DataFrame()


def load_grid_data(config: Any = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """
    Load grid energy data with fallback chain: SharePoint Lists → Legacy JSON files

    Args:
        config: Configuration object (unused for now)
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)

    Returns:
        DataFrame with grid data
    """
    logger.info("Loading grid data with fallback chain")

    # Convert string dates to date objects
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = date.fromisoformat(start_date)
        except ValueError:
            logger.warning(f"Invalid start_date format: {start_date}")
    if end_date:
        try:
            end_dt = date.fromisoformat(end_date)
        except ValueError:
            logger.warning(f"Invalid end_date format: {end_date}")

    # Try SharePoint lists first
    sharepoint_service = _get_sharepoint_service()
    if sharepoint_service:
        try:
            logger.info("Attempting to load grid data from SharePoint lists")
            df = sharepoint_service.get_grid_data(start_dt, end_dt)
            if df is not None and not df.empty:
                logger.info(f"Successfully loaded {len(df)} grid records from SharePoint")
                return df
            else:
                logger.warning("SharePoint lists returned empty grid data")
        except Exception as e:
            logger.warning(f"SharePoint lists failed for grid data: {e}")

    # Fallback to legacy JSON files (if any exist)
    logger.info("Falling back to legacy sources for grid data")
    # For now, return empty DataFrame as there's no legacy grid data
    # In the future, this could load from other JSON files if they exist
    logger.info("No legacy grid data available")
    return pd.DataFrame()


def load_diesel_data(config: Any = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """
    Load diesel generator data with fallback chain: SharePoint Lists → Legacy JSON files

    Args:
        config: Configuration object (unused for now)
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)

    Returns:
        DataFrame with diesel data
    """
    logger.info("Loading diesel data with fallback chain")

    # Convert string dates to date objects
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = date.fromisoformat(start_date)
        except ValueError:
            logger.warning(f"Invalid start_date format: {start_date}")
    if end_date:
        try:
            end_dt = date.fromisoformat(end_date)
        except ValueError:
            logger.warning(f"Invalid end_date format: {end_date}")

    # Try SharePoint lists first
    sharepoint_service = _get_sharepoint_service()
    if sharepoint_service:
        try:
            logger.info("Attempting to load diesel data from SharePoint lists")
            df = sharepoint_service.get_diesel_data(start_dt, end_dt)
            if df is not None and not df.empty:
                logger.info(f"Successfully loaded {len(df)} diesel records from SharePoint")
                return df
            else:
                logger.warning("SharePoint lists returned empty diesel data")
        except Exception as e:
            logger.warning(f"SharePoint lists failed for diesel data: {e}")

    # Fallback to legacy JSON files (if any exist)
    logger.info("Falling back to legacy sources for diesel data")
    # For now, return empty DataFrame as there's no legacy diesel data
    # In the future, this could load from other JSON files if they exist
    logger.info("No legacy diesel data available")
    return pd.DataFrame()


def load_unified_data(config: Any = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """
    Load unified energy data (combination of all sources: solar, grid, diesel)

    Args:
        config: Configuration object
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)

    Returns:
        DataFrame with unified energy data
    """
    logger.info("Loading unified energy data from all sources")

    # Load data from all sources
    solar_df = load_solar_data(config, start_date, end_date)
    grid_df = load_grid_data(config, start_date, end_date)
    diesel_df = load_diesel_data(config, start_date, end_date)

    # Combine dataframes if they have data
    dataframes = []
    if not solar_df.empty:
        solar_df['Source'] = 'Solar'
        dataframes.append(solar_df)
    if not grid_df.empty:
        grid_df['Source'] = 'Grid'
        dataframes.append(grid_df)
    if not diesel_df.empty:
        diesel_df['Source'] = 'Diesel'
        dataframes.append(diesel_df)

    if not dataframes:
        logger.warning("No data available from any source")
        return pd.DataFrame()

    # Concatenate all dataframes
    unified_df = pd.concat(dataframes, ignore_index=True, sort=False)
    logger.info(f"Unified data loaded: {len(unified_df)} total records from {len(dataframes)} sources")
    return unified_df


def load_daily_summary(config: Any = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """
    Load daily aggregated summary
    """
    return load_solar_data(config, start_date, end_date)


def load_current_metrics() -> Dict[str, Any]:
    """Get current metrics from dashboard data"""
    dashboard = load_current_dashboard_data()
    return {
        'timestamp': dashboard.get('dashboard_info', {}).get('timestamp'),
        'dc_power_kw': dashboard.get('power_data', {}).get('DC_Power_kW', 0),
        'ac_power_kw': dashboard.get('power_data', {}).get('AC_Power_kW', 0),
        'day_generation_kwh': dashboard.get('energy_data', {}).get('Day_Generation_kWh', 0),
        'total_generation_kwh': dashboard.get('energy_data', {}).get('Total_Generation_kWh', 0),
    }


def load_all(config: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Load all data sources (config, grid, solar, diesel).
    Returns: (config_dict, grid_df, solar_df, diesel_df)
    """
    if config is None:
        # Load default config or create empty one
        config = {
            "grid_rate": 7.11,  # INR per kWh
            "email": {
                "default_to": "",
                "default_cc": "",
                "subject": "Daily Energy Report — Noida Campus — {date}"
            }
        }

    grid_df = load_grid_data(config)
    solar_df = load_solar_data(config)
    diesel_df = load_diesel_data(config)

    return config, grid_df, solar_df, diesel_df


def load_solar_last7_data(config: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """
    Load last 7 days of solar data.
    """
    return load_solar_data(config)
