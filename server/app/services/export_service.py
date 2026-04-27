"""
Export service for generating Excel files from Master Data
"""
from typing import Optional
from io import BytesIO
import pandas as pd
from app.services.sharepoint_data_service import get_service as get_excel_service

def export_unified_excel(start_date: Optional[str] = None, end_date: Optional[str] = None) -> BytesIO:
    """Exports the filtered Master Data as an Excel file for user download."""
    sp_service = get_excel_service()
    df = sp_service.fetch_sheet_data("master_data")
    
    if df is not None and not df.empty and "Date" in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], errors="coerce")
        if start_date:
            df = df[df['Date'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['Date'] <= pd.to_datetime(end_date)]
        df['Date'] = df['Date'].dt.strftime("%Y-%m-%d")
    else:
        df = pd.DataFrame()

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Energy_Data', index=False)
        
    output.seek(0)
    return output

# Map legacy specific exports to the unified sheet, since it contains all data points
export_solar_excel = export_unified_excel
export_diesel_excel = export_unified_excel
export_grid_excel = export_unified_excel