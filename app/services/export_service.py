"""
Export service for generating Excel files
"""
from pathlib import Path
from typing import Optional
from io import BytesIO
import pandas as pd

from app.agents.ingestion import loader, processor, exporter
from app.core.config import get_settings

config = get_settings()


def export_unified_excel(start_date: Optional[str] = None, end_date: Optional[str] = None) -> BytesIO:
    """Export unified data to Excel"""
    # Load all data
    grid_df = loader.load_grid_data()
    solar_df = loader.load_solar_data()
    diesel_df = loader.load_diesel_data()

    # Build unified dataframe
    unified_df = processor.build_unified_dataframe(grid_df, solar_df, diesel_df)

    # Filter by date if provided
    if start_date or end_date:
        unified_df['Date'] = pd.to_datetime(unified_df['Date'])
        if start_date:
            unified_df = unified_df[unified_df['Date'] >= pd.to_datetime(start_date)]
        if end_date:
            unified_df = unified_df[unified_df['Date'] <= pd.to_datetime(end_date)]

    # Export to BytesIO
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        unified_df.to_excel(writer, sheet_name='Unified Energy Data', index=False)

    output.seek(0)
    return output


def export_grid_excel(start_date: Optional[str] = None, end_date: Optional[str] = None) -> BytesIO:
    """Export grid data to Excel"""
    grid_df = loader.load_grid_data()

    # Filter by date if provided
    if start_date or end_date:
        grid_df['Date'] = pd.to_datetime(grid_df['Date'])
        if start_date:
            grid_df = grid_df[grid_df['Date'] >= pd.to_datetime(start_date)]
        if end_date:
            grid_df = grid_df[grid_df['Date'] <= pd.to_datetime(end_date)]

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        grid_df.to_excel(writer, sheet_name='Grid Data', index=False)

    output.seek(0)
    return output


def export_solar_excel(start_date: Optional[str] = None, end_date: Optional[str] = None) -> BytesIO:
    """Export solar data to Excel"""
    solar_df = loader.load_solar_data()

    # Filter by date if provided
    if start_date or end_date:
        solar_df['Date'] = pd.to_datetime(solar_df['Date'])
        if start_date:
            solar_df = solar_df[solar_df['Date'] >= pd.to_datetime(start_date)]
        if end_date:
            solar_df = solar_df[solar_df['Date'] <= pd.to_datetime(end_date)]

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        solar_df.to_excel(writer, sheet_name='Solar Data', index=False)

    output.seek(0)
    return output


def export_diesel_excel(start_date: Optional[str] = None, end_date: Optional[str] = None) -> BytesIO:
    """Export diesel data to Excel"""
    diesel_df = loader.load_diesel_data()

    # Filter by date if provided
    if start_date or end_date:
        diesel_df['Date'] = pd.to_datetime(diesel_df['Date'])
        if start_date:
            diesel_df = diesel_df[diesel_df['Date'] >= pd.to_datetime(start_date)]
        if end_date:
            diesel_df = diesel_df[diesel_df['Date'] <= pd.to_datetime(end_date)]

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        diesel_df.to_excel(writer, sheet_name='Diesel Data', index=False)

    output.seek(0)
    return output


def export_ecs_excel() -> BytesIO:
    """Export in ECS format"""
    grid_df = loader.load_grid_data()
    solar_df = loader.load_solar_data()
    diesel_df = loader.load_diesel_data()
    unified_df = processor.build_unified_dataframe(grid_df, solar_df, diesel_df)

    ecs_bytes = exporter.export_ecs_style_xlsx(unified_df)
    output = BytesIO(ecs_bytes)
    output.seek(0)
    return output

