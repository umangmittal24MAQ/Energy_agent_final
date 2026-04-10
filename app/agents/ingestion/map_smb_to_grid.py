import json
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

# Fix Unicode encoding on Windows
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def safe_power_to_kw(power_w):
    """Convert power from watts to kilowatts, returning 0 for null/zero values"""
    if not power_w:  # Handles None, 0, False, etc.
        return 0
    return power_w / 1000
 
def map_smb_data_to_grid():
    """
    Maps SMB data from filtered_solar_panel_data.json into a data grid format
    matching the table structure: Date, Day, Time, Solar Units Generated (kWh),
    Inverter Status, SMB1 (kWh), SMB2 (kWh), SMB3 (kWh), SMB4 (kWh), SMB5 (kWh)
    """
   
    # Get the directory of the current script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Load the solar panel data
    input_file = Path(script_dir) / "filtered_solar_panel_data.json"
    if not input_file.exists():
        print("Error: filtered_solar_panel_data.json not found")
        return
   
    with open(input_file, 'r') as f:
        data = json.load(f)
   
    # Extract relevant information
    extraction_info = data.get("data_extraction_info", {})
    extraction_date = extraction_info.get("extraction_date", "")
    last_update = extraction_info.get("last_update", "")
   
    # Parse datetime
    if last_update:
        dt = datetime.fromisoformat(last_update.replace('Z', '+00:00'))
        time_str = dt.strftime("%H:%M:%S")
        day_str = dt.strftime("%A")  # Full day name
    else:
        time_str = ""
        day_str = ""
   
    # Get generation data
    generation_summary = data.get("generation_summary", {})
    today_generation_kwh = generation_summary.get("today_generation_kWh", 0)
   
    # Get inverter status
    device_status = data.get("device_status", {})
    plant_status = device_status.get("plant_status", {})
    inverter_status = plant_status.get("status", "Unknown")
   
    # Get SMB data - convert from Watts to kWh (power in Watts, so we'll use as-is for W or convert)
    smbboxes = device_status.get("smbboxes", {})
   
    # Create grid row
    grid_row = {
        "Date": extraction_date,
        "Day": day_str,
        "Time": time_str,
        "Solar Units Generated (kWh)": today_generation_kwh,
        "Inverter Status": inverter_status,
        "SMB1 (kW)": safe_power_to_kw(smbboxes.get("SMB_1", {}).get("power_w")),
        "SMB1 Status": smbboxes.get("SMB_1", {}).get("status", "Unknown"),
        "SMB2 (kW)": safe_power_to_kw(smbboxes.get("SMB_2", {}).get("power_w")),
        "SMB2 Status": smbboxes.get("SMB_2", {}).get("status", "Unknown"),
        "SMB3 (kW)": safe_power_to_kw(smbboxes.get("SMB_3", {}).get("power_w")),
        "SMB3 Status": smbboxes.get("SMB_3", {}).get("status", "Unknown"),
        "SMB4 (kW)": safe_power_to_kw(smbboxes.get("SMB_4", {}).get("power_w")),
        "SMB4 Status": smbboxes.get("SMB_4", {}).get("status", "Unknown"),
        "SMB5 (kW)": safe_power_to_kw(smbboxes.get("SMB_5", {}).get("power_w")),
        "SMB5 Status": smbboxes.get("SMB_5", {}).get("status", "Unknown"),
    }
   
    print("\n=== SMB DATA MAPPED TO GRID ===\n")
    print(f"Date: {grid_row['Date']}")
    print(f"Day: {grid_row['Day']}")
    print(f"Time: {grid_row['Time']}")
    print(f"Solar Units Generated: {grid_row['Solar Units Generated (kWh)']} kWh")
    print(f"Inverter Status: {grid_row['Inverter Status']}")
    print(f"\nSMB Power Output & Status:")
    print(f"  SMB1: {grid_row['SMB1 (kW)']:.2f} kW - Status: {grid_row['SMB1 Status']}")
    print(f"  SMB2: {grid_row['SMB2 (kW)']:.2f} kW - Status: {grid_row['SMB2 Status']}")
    print(f"  SMB3: {grid_row['SMB3 (kW)']:.2f} kW - Status: {grid_row['SMB3 Status']}")
    print(f"  SMB4: {grid_row['SMB4 (kW)']:.2f} kW - Status: {grid_row['SMB4 Status']}")
    print(f"  SMB5: {grid_row['SMB5 (kW)']:.2f} kW - Status: {grid_row['SMB5 Status']}")
   
    # Save to CSV
    output_csv = Path(script_dir) / "smb_data_grid.csv"
    with open(output_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=grid_row.keys())
        writer.writeheader()
        writer.writerow(grid_row)
   
    print(f"\n✓ Grid data saved to: {output_csv}")
   
    # Also save as JSON for reference
    output_json = Path(script_dir) / "smb_data_grid.json"
    with open(output_json, 'w') as f:
        json.dump([grid_row], f, indent=2)
   
    print(f"✓ Grid data saved to: {output_json}")
   
    # Create summary statistics
    create_smb_summary(data)
 
def create_smb_summary(data):
    """Create a detailed SMB summary report"""
    device_status = data.get("device_status", {})
    smbboxes = device_status.get("smbboxes", {})
   
    print("\n=== SMB DETAILED STATUS ===\n")
   
    total_power = 0
    for smb_name, smb_data in smbboxes.items():
        power_w = smb_data.get("power_w", 0)
        status = smb_data.get("status", "Unknown")
        power_kw = safe_power_to_kw(power_w)
        total_power += power_kw
       
        print(f"{smb_name}:")
        print(f"  Status: {status}")
        print(f"  Power Output: {power_kw:.2f} kW ({power_w} W)")
        print()
   
    print(f"Total System Power: {total_power:.2f} kW\n")
 
if __name__ == "__main__":
    map_smb_data_to_grid()