"""
Data Exporter Module
Handles exporting data to various formats (Excel, CSV, etc.)
"""
import pandas as pd
from io import BytesIO
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def export_to_excel(df: pd.DataFrame, include_metadata: bool = True) -> bytes:
    """
    Export data to Excel format
    Returns bytes that can be sent as a file response
    """
    if df.empty:
        logger.warning("Attempting to export empty DataFrame")
        return BytesIO().getvalue()
    
    output = BytesIO()
    
    try:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Data', index=False)
            
            if include_metadata:
                # Add metadata sheet
                metadata_df = pd.DataFrame([{
                    'Export Date': pd.Timestamp.now().isoformat(),
                    'Total Records': len(df),
                    'Data Source': 'Energy Dashboard API'
                }])
                metadata_df.to_excel(writer, sheet_name='Metadata', index=False)
        
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        logger.error(f"Error exporting to Excel: {e}")
        return BytesIO().getvalue()


def export_to_csv(df: pd.DataFrame) -> bytes:
    """
    Export data to CSV format
    Returns bytes that can be sent as a file response
    """
    if df.empty:
        logger.warning("Attempting to export empty DataFrame")
        return b""
    
    try:
        csv_bytes = df.to_csv(index=False).encode('utf-8')
        return csv_bytes
    except Exception as e:
        logger.error(f"Error exporting to CSV: {e}")
        return b""


def export_solar_data(df: pd.DataFrame) -> bytes:
    """Export solar data to Excel"""
    return export_to_excel(df)


def export_grid_data(df: pd.DataFrame) -> bytes:
    """Export grid data to Excel"""
    return export_to_excel(df)


def export_diesel_data(df: pd.DataFrame) -> bytes:
    """Export diesel data to Excel"""
    return export_to_excel(df)


def export_unified_data(df: pd.DataFrame) -> bytes:
    """Export unified energy data to Excel"""
    return export_to_excel(df)


def export_ecs_data() -> bytes:
    """
    Export ECS (Energy Control System) summary data
    Can combine multiple sources into a comprehensive report
    """
    # Placeholder for ECS export functionality
    summary_data = {
        'System': ['Solar', 'Grid', 'Diesel'],
        'Status': ['Active', 'Active', 'Standby'],
        'Current Power (kW)': [0, 0, 0],
        'Daily Generation (kWh)': [0, 0, 0]
    }
    
    df_summary = pd.DataFrame(summary_data)
    return export_to_excel(df_summary, include_metadata=True)
