import json
from datetime import datetime
import os
import sys

# Fix Unicode encoding on Windows
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Get the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load the captured API data
with open(os.path.join(script_dir, 'captured_api_data.json'), 'r') as f:
    api_data = json.load(f)

# Extract dashboard visible fields
dashboard_data = {
    "dashboard_info": {
        "last_update": datetime.now().isoformat(),
        "timestamp": None
    },
    "plant_capacity": {
        "DC_Capacity_kWp": None,
        "AC_Capacity_kW": None
    },
    "power_data": {
        "DC_Power_kW": None,
        "AC_Power_kW": None,
        "Current_Total_A": None,
        "Current_Average_A": None,
        "Active_Power_kW": None,
        "Apparent_Power_kVA": None,
        "Power_Factor": None,
        "Frequency_Hz": None
    },
    "voltage_data": {
        "Voltage_VLL_Phase_to_Phase_V": None,
        "Voltage_VLN_Phase_to_Neutral_V": None,
        "Voltage_V1_V": None,
        "Voltage_V2_V": None,
        "Voltage_V3_V": None
    },
    "energy_data": {
        "Day_Generation_kWh": None,
        "Day_Import_kWh": None,
        "Day_Export_kWh": None,
        "Total_Generation_kWh": None,
        "Total_Import_kWh": None,
        "Total_Export_kWh": None
    },
    "time_data": {
        "Start_Time": None,
        "End_Time": None
    }
}

try:
    # Process first response (change_plant)
    plant_info = api_data[0]['data']['plantInfo']
    
    # Plant capacities
    dashboard_data['plant_capacity']['DC_Capacity_kWp'] = float(plant_info.get('dc_size', 0))
    dashboard_data['plant_capacity']['AC_Capacity_kW'] = float(plant_info.get('ac_size', 0))
    
    # Process second response (gen_info with live data)
    live_data = api_data[1]['data']
    dashboard_data['dashboard_info']['timestamp'] = live_data.get('serverTime')
    
    # Extract from lastLogData
    last_log = live_data['lastLogData']
    
    # Plant data (DC Power)
    if 'plant' in last_log:
        plant_dcw = last_log['plant'].get('DCW', 0)
        dashboard_data['power_data']['DC_Power_kW'] = round(plant_dcw / 1000, 2)
    
    # Inverter data - sum all inverters for DC and AC power
    if 'inverter' in last_log:
        total_dc_w = 0
        total_ac_w = 0
        inverter_details = []
        
        for inv_id, inv_data in last_log['inverter'].items():
            if isinstance(inv_data, dict):
                inv_detail = {
                    "id": inv_id,
                    "DC_V": inv_data.get('DC_V', 0),
                    "DC_I": inv_data.get('DC_I', 0),
                    "DC_W": inv_data.get('DC_W', 0),
                    "AC_Voltage": inv_data.get('VT', 0),
                    "AC_Current": inv_data.get('IT', 0),
                    "AC_Power": inv_data.get('WT', 0),
                    "PF": inv_data.get('PFT', 0),
                    "Frequency": inv_data.get('FREQ', 0)
                }
                inverter_details.append(inv_detail)
                total_dc_w += inv_data.get('DC_W', 0)
                total_ac_w += inv_data.get('WT', 0)
        
        dashboard_data['power_data']['DC_Power_kW'] = round(total_dc_w / 1000, 2)
        dashboard_data['power_data']['AC_Power_kW'] = round(total_ac_w / 1000, 2)
    
    # Meter data - get primary meter (SOLAR_METER or first online meter)
    if 'meter' in last_log:
        # Use the first online meter or the primary grid meter
        primary_meter = None
        for meter_id, meter_data in last_log['meter'].items():
            if isinstance(meter_data, dict) and meter_data.get('meter_online', 0) == 1:
                if primary_meter is None:
                    primary_meter = meter_data
                # Check if this is the solar meter by looking at the device name pattern
                if meter_id == "595a743279372f72384f6c776c61336756712f66455833732f5148324f4763375841796133424c63656b673d":
                    primary_meter = meter_data
                    break
        
        if primary_meter is None and last_log['meter']:
            # Fallback to any available meter
            for meter_data in last_log['meter'].values():
                if isinstance(meter_data, dict):
                    primary_meter = meter_data
                    break
        
        if primary_meter:
            # Voltage data
            dashboard_data['voltage_data']['Voltage_VLL_Phase_to_Phase_V'] = round(primary_meter.get('VLL', 0), 2)
            dashboard_data['voltage_data']['Voltage_VLN_Phase_to_Neutral_V'] = round(primary_meter.get('VLN', 0), 2)
            dashboard_data['voltage_data']['Voltage_V1_V'] = round(primary_meter.get('V1', 0), 2)
            dashboard_data['voltage_data']['Voltage_V2_V'] = round(primary_meter.get('V2', 0), 2)
            dashboard_data['voltage_data']['Voltage_V3_V'] = round(primary_meter.get('V3', 0), 2)
            
            # Current data
            i1 = primary_meter.get('I1', 0)
            i2 = primary_meter.get('I2', 0)
            i3 = primary_meter.get('I3', 0)
            total_current = i1 + i2 + i3
            avg_current = total_current / 3 if total_current > 0 else 0
            
            dashboard_data['power_data']['Current_Total_A'] = round(total_current, 2)
            dashboard_data['power_data']['Current_Average_A'] = round(avg_current, 2)
            
            # Power data
            dashboard_data['power_data']['Active_Power_kW'] = round(primary_meter.get('WT', 0) / 1000, 2)
            dashboard_data['power_data']['Apparent_Power_kVA'] = round(primary_meter.get('VAT', 0) / 1000, 2)
            dashboard_data['power_data']['Power_Factor'] = round(primary_meter.get('PFT', 0), 3)
            dashboard_data['power_data']['Frequency_Hz'] = round(primary_meter.get('FREQ', 0), 2)
            
            # Energy data
            dashboard_data['energy_data']['Total_Import_kWh'] = round(primary_meter.get('WHImp', 0), 2)
            dashboard_data['energy_data']['Total_Export_kWh'] = round(primary_meter.get('WHExp', 0), 2)
    
    # Extract day generation from inverters
    if 'inverter' in last_log:
        total_day_generation = 0
        for inv_id, inv_data in last_log['inverter'].items():
            if isinstance(inv_data, dict):
                total_day_generation += inv_data.get('WHDay', 0)
        dashboard_data['energy_data']['Day_Generation_kWh'] = round(total_day_generation, 2)
    
    # Add additional plant info
    dashboard_data['plant_info'] = {
        "plant_name": plant_info.get('plant_name'),
        "plant_address": plant_info.get('plant_address'),
        "latitude": plant_info.get('latitude'),
        "longitude": plant_info.get('longitude'),
        "time_zone": plant_info.get('time_zone'),
        "plant_installed_date": plant_info.get('plant_installed'),
        "device_model": plant_info.get('device_model'),
        "device_sw_version": '.'.join(plant_info.get('device_sw_version', [])),
        "device_hw_version": '.'.join(plant_info.get('device_hw_version', []))
    }
    
    # Save to filtered output
    output_file = os.path.join(script_dir, 'filtered_dashboard_data.json')
    with open(output_file, 'w') as f:
        json.dump(dashboard_data, f, indent=2)
    
    print("✓ Dashboard data extracted successfully!")
    print("\nExtracted Dashboard Data Summary:")
    print(f"DC Capacity: {dashboard_data['plant_capacity']['DC_Capacity_kWp']} kWp")
    print(f"AC Capacity: {dashboard_data['plant_capacity']['AC_Capacity_kW']} kW")
    print(f"DC Power: {dashboard_data['power_data']['DC_Power_kW']} kW")
    print(f"AC Power: {dashboard_data['power_data']['AC_Power_kW']} kW")
    print(f"Voltage VLL: {dashboard_data['voltage_data']['Voltage_VLL_Phase_to_Phase_V']} V")
    print(f"Voltage VLN: {dashboard_data['voltage_data']['Voltage_VLN_Phase_to_Neutral_V']} V")
    print(f"Total Current: {dashboard_data['power_data']['Current_Total_A']} A")
    print(f"Average Current: {dashboard_data['power_data']['Current_Average_A']} A")
    print(f"Active Power: {dashboard_data['power_data']['Active_Power_kW']} kW")
    print(f"Frequency: {dashboard_data['power_data']['Frequency_Hz']} Hz")
    print(f"Power Factor: {dashboard_data['power_data']['Power_Factor']}")
    print(f"Total Import: {dashboard_data['energy_data']['Total_Import_kWh']} kWh")
    print(f"Total Export: {dashboard_data['energy_data']['Total_Export_kWh']} kWh")
    print(f"Day Generation: {dashboard_data['energy_data']['Day_Generation_kWh']} kWh")
    print(f"\nFull data saved to: {output_file}")
    
except Exception as e:
    print(f"Error extracting dashboard data: {str(e)}")
    import traceback
    traceback.print_exc()
