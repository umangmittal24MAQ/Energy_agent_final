"""
Data Processor Module
Handles data transformation and processing for API responses
"""
import pandas as pd
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def process_solar_data(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Process solar data DataFrame into API response format
    """
    if df.empty:
        return {
            'data': [],
            'metadata': {
                'total_records': 0,
                'date_range': {'start': None, 'end': None}
            }
        }
    
    # Convert DataFrame to list of dicts
    data = df.to_dict('records')
    
    # Calculate metadata
    dates = pd.to_datetime(df['Date'], errors='coerce').dropna().unique()
    
    return {
        'data': data,
        'metadata': {
            'total_records': len(data),
            'date_range': {
                'start': str(dates[0]) if len(dates) > 0 else None,
                'end': str(dates[-1]) if len(dates) > 0 else None
            }
        }
    }


def process_grid_data(df: pd.DataFrame) -> Dict[str, Any]:
    """Process grid energy data"""
    return process_solar_data(df)  # Same format for now


def process_diesel_data(df: pd.DataFrame) -> Dict[str, Any]:
    """Process diesel generator data"""
    return process_solar_data(df)


def build_unified_dataframe(grid_df: pd.DataFrame, solar_df: pd.DataFrame, diesel_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build unified dataframe by combining grid, solar, and diesel data
    
    Args:
        grid_df: Grid energy data
        solar_df: Solar energy data  
        diesel_df: Diesel generator data
        
    Returns:
        Combined dataframe with all energy sources
    """
    try:
        # Start with empty dataframe
        unified_df = pd.DataFrame()
        
        # Add grid data if available
        if not grid_df.empty:
            grid_df = grid_df.copy()
            grid_df['source'] = 'grid'
            unified_df = pd.concat([unified_df, grid_df], ignore_index=True)
            
        # Add solar data if available
        if not solar_df.empty:
            solar_df = solar_df.copy()
            solar_df['source'] = 'solar'
            unified_df = pd.concat([unified_df, solar_df], ignore_index=True)
            
        # Add diesel data if available
        if not diesel_df.empty:
            diesel_df = diesel_df.copy()
            diesel_df['source'] = 'diesel'
            unified_df = pd.concat([unified_df, diesel_df], ignore_index=True)
            
        logger.info(f"Built unified dataframe with {len(unified_df)} records from {len(grid_df)} grid, {len(solar_df)} solar, {len(diesel_df)} diesel")
        return unified_df
        
    except Exception as e:
        logger.error(f"Error building unified dataframe: {e}")
        return pd.DataFrame()


def process_unified_data(df: pd.DataFrame) -> Dict[str, Any]:
    """Process unified energy data"""
    return process_solar_data(df)


def process_daily_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Process daily summary data with aggregations
    """
    if df.empty:
        return {
            'data': [],
            'metadata': {
                'total_records': 0,
                'date_range': {'start': None, 'end': None}
            }
        }
    
    # Group by date and aggregate
    daily_agg = df.groupby('Date').agg({
        'generation_wh': 'sum',
        'start_value': 'first',
        'end_value': 'last'
    }).reset_index()
    
    data = daily_agg.to_dict('records')
    
    return {
        'data': data,
        'metadata': {
            'total_records': len(data),
            'date_range': {
                'start': daily_agg['Date'].min() if len(daily_agg) > 0 else None,
                'end': daily_agg['Date'].max() if len(daily_agg) > 0 else None
            }
        }
    }


def calculate_kpis(df: pd.DataFrame, metric: str = 'generation_wh') -> Dict[str, Any]:
    """
    Calculate KPIs from data
    """
    if df.empty:
        return {
            'total': 0,
            'average': 0,
            'min': 0,
            'max': 0,
            'latest': 0
        }
    
    if metric not in df.columns:
        return {
            'total': 0,
            'average': 0,
            'min': 0,
            'max': 0,
            'latest': 0
        }
    
    col_data = pd.to_numeric(df[metric], errors='coerce').dropna()
    
    return {
        'total': float(col_data.sum()),
        'average': float(col_data.mean()),
        'min': float(col_data.min()),
        'max': float(col_data.max()),
        'latest': float(col_data.iloc[-1]) if len(col_data) > 0 else 0
    }


def process_kpi_overview(current_metrics: Dict[str, Any], historical_data: pd.DataFrame) -> Dict[str, Any]:
    """
    Process overview KPIs
    """
    solar_kpi = calculate_kpis(historical_data, 'generation_wh')
    
    return {
        'solar': {
            'current_power_kw': current_metrics.get('ac_power_kw', 0),
            'day_generation_kwh': current_metrics.get('day_generation_kwh', 0),
            'total_generation_kwh': current_metrics.get('total_generation_kwh', 0),
            'kpis': solar_kpi
        },
        'grid': {
            'current_power_kw': 0,
            'day_consumption_kwh': 0,
            'kpis': calculate_kpis(historical_data)
        },
        'diesel': {
            'current_power_kw': 0,
            'day_generation_kwh': 0,
            'kpis': calculate_kpis(historical_data)
        }
    }


def process_kpi_solar(df: pd.DataFrame) -> Dict[str, Any]:
    """Process solar KPIs"""
    return {
        'solar': calculate_kpis(df, 'generation_wh'),
        'timestamp': datetime.now().isoformat()
    }


def process_kpi_grid(df: pd.DataFrame) -> Dict[str, Any]:
    """Process grid KPIs"""
    return {
        'grid': calculate_kpis(df),
        'timestamp': datetime.now().isoformat()
    }


def process_kpi_diesel(df: pd.DataFrame) -> Dict[str, Any]:
    """Process diesel KPIs"""
    return {
        'diesel': calculate_kpis(df),
        'timestamp': datetime.now().isoformat()
    }
