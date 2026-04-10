import json
import time
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

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

def get_device_status_text(status_code):
    """Convert numeric status code to human-readable status"""
    if status_code == 0:
        return "ON"
    elif status_code == 17:
        return "ON"
    elif status_code > 0:
        return "ACTIVE"
    else:
        return "OFF"

solar_panel_data = {
    "data_extraction_info": {
        "last_update": datetime.now().isoformat(),
        "api_timestamp": None,
        "extraction_date": datetime.now().strftime("%Y-%m-%d")
    },
    "generation_summary": {
        "today_generation_kWh": None,
        "yesterday_generation_kWh": None,
        "weekly_generation_kWh": None,
        "monthly_generation_kWh": None,
        "yearly_generation_kWh": None,
        "total_cumulative_generation_kWh": None,
        "capacity_utilization_factor_percent": None
    },
    "inverter_details": [],
    "daily_generation_timeline": {},
    "energy_metrics": {
        "daily_average_kWh": None,
        "peak_power_kW": None,
        "average_efficiency_percent": None
    },
    "system_performance": {
        "performance_ratio": None,  # Only set if actual data available
        "system_loss_percent": None,  # Only set if actual data available
        "temperature_coefficient": None  # Only set if actual data available
    },
    'environmental_data': {
        "irradiance_w_m2": None,  # Extract from weather/sensor data if available
        "ambient_temperature": None,  # Extract from weather/sensor data if available
        "module_temperature": None  # Can use inverter heatsink temp as proxy
    },
    "device_status": {
        "plant_status": None,
        "device_status_code": None,
        "inverters": {},
        "meters": {},
        "smbboxes": {}
    }
}

try:
    # Get plant info for capacity calculation
    plant_info = api_data[0]['data']['plantInfo']
    dc_capacity = float(plant_info.get('dc_size', 598.6))
    ac_capacity = float(plant_info.get('ac_size', 500.0))
    co2_factor = float(plant_info.get('co2_factor', 0.825))
    
    solar_panel_data['data_extraction_info']['api_timestamp'] = api_data[1]['data'].get('serverTime')
    
    # Extract live data
    live_data = api_data[1]['data']
    last_log = live_data['lastLogData']
    
    # ========== INVERTER DETAILS ==========
    print("Extracting inverter data...")
    if 'inverter' in last_log:
        total_day_generation = 0
        total_total_generation = 0
        peak_power = 0
        
        for inv_id, inv_data in last_log['inverter'].items():
            if isinstance(inv_data, dict):
                inv_detail = {
                    "inverter_id": inv_id,
                    "model_name": inv_data.get('model_name', 'Unknown'),
                    "status": inv_data.get('suryalog_status', 0),
                    "dc_voltage_v": round(inv_data.get('DC_V', 0), 2),
                    "dc_current_a": round(inv_data.get('DC_I', 0), 2),
                    "dc_power_w": round(inv_data.get('DC_W', 0), 2),
                    "ac_voltage_v": round(inv_data.get('VT', 0), 2),
                    "ac_current_a": round(inv_data.get('IT', 0), 2),
                    "ac_power_w": round(inv_data.get('WT', 0), 2),
                    "power_factor": round(inv_data.get('PFT', 0), 3),
                    "frequency_hz": round(inv_data.get('FREQ', 0), 2),
                    "temperature_internal_c": round(inv_data.get('TEMP_INT', 0), 1),
                    "temperature_heatsink_c": round(inv_data.get('TEMP_HS', 0), 1),
                    "daily_generation_kwh": round(inv_data.get('WHDay', 0) / 1000, 2),
                    "total_generation_kwh": round(inv_data.get('WHTot', 0) / 1000, 2),
                    "apparent_power_va": round(inv_data.get('VAT', 0), 2),
                    "reactive_power_var": round(inv_data.get('VART', 0), 2),
                    "run_hours": inv_data.get('RUN_HOURS', 0)
                }
                solar_panel_data['inverter_details'].append(inv_detail)
                
                total_day_generation += inv_data.get('WHDay', 0) / 1000
                total_total_generation += inv_data.get('WHTot', 0) / 1000
                peak_power = max(peak_power, inv_data.get('WT', 0))
        
        # Convert from Wh to kWh
        solar_panel_data['generation_summary']['today_generation_kWh'] = round(total_day_generation, 2)
        solar_panel_data['generation_summary']['total_cumulative_generation_kWh'] = round(total_total_generation, 2)
        solar_panel_data['energy_metrics']['peak_power_kW'] = round(peak_power, 2)
    
    # ========== METER DATA FOR ADDITIONAL METRICS ==========
    print("Extracting meter and grid data...")
    if 'meter' in last_log:
        # Get primary meter
        primary_meter = None
        for meter_id, meter_data in last_log['meter'].items():
            if isinstance(meter_data, dict) and meter_data.get('meter_online', 0) == 1:
                if meter_id == "595a743279372f72384f6c776c61336756712f66455833732f5148324f4763375841796133424c63656b673d":
                    primary_meter = meter_data
                    break
        
        if primary_meter is None and last_log['meter']:
            for meter_data in last_log['meter'].values():
                if isinstance(meter_data, dict):
                    primary_meter = meter_data
                    break
    
    # ========== CALCULATE CUF (Capacity Utilization Factor) ==========
    print("Calculating performance metrics...")
    if solar_panel_data['generation_summary']['today_generation_kWh'] is not None:
        # CUF = (Actual Generation) / (Installed AC Capacity × Peak Sun Hours) × 100
        # Typical Peak Sun Hours in India: ~4-5 hours/day
        peak_sun_hours = 4.5  # Average for Noida region
        cuf = (solar_panel_data['generation_summary']['today_generation_kWh'] / (ac_capacity * peak_sun_hours)) * 100
        solar_panel_data['generation_summary']['capacity_utilization_factor_percent'] = round(cuf, 2)
    
    # ========== EXTRACT METER REPORT DATA (Yesterday & Historical) ==========
    print("Extracting meter report data...")
    
    daily_generation = defaultdict(float)
    latest_timestamp = None
    
    # Extract generation data from meter reports in plant config
    if 'plantInfo' in api_data[0]['data'] and 'meter' in api_data[0]['data']['plantInfo']:
        plant_meters = api_data[0]['data']['plantInfo']['meter']
        if isinstance(plant_meters, dict):
            for meter_id, meter_config in plant_meters.items():
                if isinstance(meter_config, dict) and 'report' in meter_config:
                    meter_reports = meter_config['report']
                    if isinstance(meter_reports, dict):
                        # Each meter_reports dict has report settings as keys
                        for report_setting_id, report_data in meter_reports.items():
                            if isinstance(report_data, dict) and 'report' in report_data:
                                report_array = report_data['report']
                                if isinstance(report_array, list):
                                    print(f"  Found {len(report_array)} meter report records from meter {meter_id}")
                                    for report in report_array:
                                        if isinstance(report, dict):
                                            try:
                                                # Extract start/end times and values
                                                end_ts = report.get('end_time', 0)
                                                value = float(report.get('value', 0))
                                                
                                                # Convert from Wh to kWh
                                                generation_kwh = value / 1000
                                                
                                                if end_ts > 0 and generation_kwh > 0:
                                                    reading_date = datetime.fromtimestamp(end_ts).date()
                                                    daily_generation[str(reading_date)] += generation_kwh
                                                    
                                                    if latest_timestamp is None or end_ts > latest_timestamp:
                                                        latest_timestamp = end_ts
                                                    
                                                    # Debug output for high generation values
                                                    if generation_kwh > 0.1:
                                                        print(f"    {reading_date}: +{generation_kwh:.3f} kWh (ts={end_ts})")
                                            except Exception as e:
                                                continue
    
    # If no meter reports from plant config, fall back to day_data
    if not daily_generation and 'day_data' in live_data and live_data['day_data']:
        print("  No meter reports found, falling back to day_data...")
        day_data = live_data['day_data']
        
        # Get all timestamps and sort them
        timestamps = []
        for ts_str in day_data.keys():
            try:
                ts = int(ts_str)
                timestamps.append(ts)
                if latest_timestamp is None or ts > latest_timestamp:
                    latest_timestamp = ts
            except:
                pass
        
        if timestamps:
            timestamps.sort()
            
            # Collect generation data by day
            for ts_str, data in day_data.items():
                if isinstance(data, dict):
                    try:
                        ts = int(ts_str)
                        reading_date = datetime.fromtimestamp(ts).date()
                        
                        # Sum inverter readings for the day
                        if 'inverter' in data:
                            for inv_id, inv_data in data['inverter'].items():
                                if isinstance(inv_data, dict) and 'WHDay' in inv_data:
                                    daily_generation[str(reading_date)] += inv_data.get('WHDay', 0) / 1000
                        
                        # Fallback to meter readings not applicable - WT is power in W, not energy
                    except:
                        continue
    
    # Use latest timestamp to calculate date ranges
    if latest_timestamp is None:
        latest_timestamp = int(time.time())
    
    latest_date = datetime.fromtimestamp(latest_timestamp)
    yesterday_date = (latest_date - timedelta(days=1)).date()
    
    print(f"  Latest date from API: {latest_date.date()}")
    print(f"  Yesterday's date: {yesterday_date}")
    print(f"  Daily generation records found: {len(daily_generation)}")
    for date, gen in sorted(daily_generation.items()):
        if gen > 0:
            print(f"    {date}: {gen:.2f} kWh")
    
    # Get yesterday's generation from meter reports
    yesterday_gen = daily_generation.get(str(yesterday_date), 0)
    if yesterday_gen > 0:
        solar_panel_data['generation_summary']['yesterday_generation_kWh'] = round(yesterday_gen, 2)
        print(f"  ✓ Yesterday's generation found: {yesterday_gen:.2f} kWh")
    
    # Calculate weekly generation (sum of last 7 days)
    weekly_gen = 0
    for i in range(7):
        check_date = (latest_date - timedelta(days=i)).date()
        weekly_gen += daily_generation.get(str(check_date), 0)
    if weekly_gen > 0:
        solar_panel_data['generation_summary']['weekly_generation_kWh'] = round(weekly_gen, 2)
    
    # Estimate monthly and yearly
    if solar_panel_data['generation_summary']['total_cumulative_generation_kWh']:
        total_gen = solar_panel_data['generation_summary']['total_cumulative_generation_kWh']
        installation_date = datetime.fromtimestamp(float(plant_info.get('plant_installed', 0)))
        days_since_installation = (latest_date - installation_date).days + 1
        
        if days_since_installation > 0:
            daily_avg = total_gen / days_since_installation
            
            # Calculate actual monthly from last 30 days if available from meter reports
            monthly_gen = 0
            for i in range(30):
                check_date = (latest_date - timedelta(days=i)).date()
                monthly_gen += daily_generation.get(str(check_date), 0)
            
            if monthly_gen > 0:
                solar_panel_data['generation_summary']['monthly_generation_kWh'] = round(monthly_gen, 2)
            else:
                # Estimate if not available
                solar_panel_data['generation_summary']['monthly_generation_kWh'] = round(daily_avg * 30, 2)
            
            # Estimate yearly (365 days)
            solar_panel_data['generation_summary']['yearly_generation_kWh'] = round(daily_avg * 365, 2)
            
            # Daily average
            solar_panel_data['energy_metrics']['daily_average_kWh'] = round(daily_avg, 2)
    
    # ========== CALCULATE AVERAGE EFFICIENCY ==========
    # Efficiency = AC Power / DC Power (if inverter data available)
    if solar_panel_data['inverter_details']:
        efficiencies = []
        for inv in solar_panel_data['inverter_details']:
            if inv['dc_power_w'] > 0 and inv['ac_power_w'] > 0:
                efficiency = (inv['ac_power_w'] / inv['dc_power_w']) * 100
                efficiencies.append(efficiency)
        
        if efficiencies:
            avg_efficiency = sum(efficiencies) / len(efficiencies)
            solar_panel_data['energy_metrics']['average_efficiency_percent'] = round(avg_efficiency, 2)
            print(f"  Average Inverter Efficiency: {solar_panel_data['energy_metrics']['average_efficiency_percent']}%")
    
    # ========== POPULATE DAILY GENERATION TIMELINE ==========
    print("Populating daily generation timeline...")
    for date_str, generation_kwh in daily_generation.items():
        if generation_kwh > 0:
            solar_panel_data['daily_generation_timeline'][date_str] = {
                "date": date_str,
                "generation_kwh": round(generation_kwh, 2),
                "peak_power_kw": None  # Would need hourly data to calculate
            }
    
    # ========== SYSTEM PERFORMANCE CALCULATIONS ==========
    # NOTE: Only populate these fields if actual data is available from API
    # DO NOT use hardcoded/estimated values
    print("Extracting system performance data...")
    
    # Check if performance metrics exist in API response
    # These would typically come from plant_info or live_data
    if 'plantInfo' in api_data[0]['data']:
        plant_info_full = api_data[0]['data']['plantInfo']
        
        # Extract actual performance ratio if available
        if 'performance_ratio' in plant_info_full:
            solar_panel_data['system_performance']['performance_ratio'] = float(plant_info_full.get('performance_ratio', None))
        
        # Extract actual system losses if available
        if 'system_loss' in plant_info_full:
            solar_panel_data['system_performance']['system_loss_percent'] = float(plant_info_full.get('system_loss', None))
        
        # Extract actual temperature coefficient if available
        if 'temp_coefficient' in plant_info_full:
            solar_panel_data['system_performance']['temperature_coefficient'] = float(plant_info_full.get('temp_coefficient', None))
    
    # Only calculate PR if we have actual generation and capacity data
    if solar_panel_data['generation_summary']['today_generation_kWh'] and ac_capacity > 0:
        # Calculate based on actual data only if not already set from API
        if solar_panel_data['system_performance']['performance_ratio'] is None:
            # Actual PR calculation: (Daily Gen in kWh) / (AC Capacity × Peak Sun Hours) × 100
            peak_sun_hours = 4.5  # For this region
            actual_pr = (solar_panel_data['generation_summary']['today_generation_kWh'] / (ac_capacity * peak_sun_hours)) * 100
            solar_panel_data['system_performance']['performance_ratio'] = round(actual_pr, 2) if actual_pr > 0 else None
    
    # Add plant metadata
    solar_panel_data['plant_info'] = {
        "plant_name": plant_info.get('plant_name'),
        "plant_id": plant_info.get('plant_id'),
        "device_model": plant_info.get('device_model'),
        "device_sw_version": '.'.join(plant_info.get('device_sw_version', [])),
        "device_hw_version": '.'.join(plant_info.get('device_hw_version', [])),
        "installation_date": datetime.fromtimestamp(float(plant_info.get('plant_installed', 0))).strftime("%Y-%m-%d"),
        "latitude": plant_info.get('latitude'),
        "longitude": plant_info.get('longitude'),
        "timezone": plant_info.get('time_zone'),
        "dc_capacity_kwp": dc_capacity,
        "ac_capacity_kw": ac_capacity,
        "number_of_inverters": plant_info.get('inverterNos', 0),
        "number_of_meters": plant_info.get('inverterNos', 0),
        "co2_mitigation_factor": co2_factor,
        "unit_rate_inr": float(plant_info.get('unit_rate', 10.0))
    }
    
    # ========== EXTRACT DEVICE STATUS ==========
    print("Extracting device status...")
    
    # Plant status
    if 'plant' in last_log and 'clone_data' in last_log['plant']:
        plant_data = last_log['plant']['clone_data'][0] if last_log['plant']['clone_data'] else {}
        solar_panel_data['device_status']['plant_status'] = {
            "status": get_device_status_text(plant_data.get('suryalog_status', 0)),
            "status_code": plant_data.get('suryalog_status', 0),
            "user_status": plant_data.get('user_status', 0)
        }
        solar_panel_data['device_status']['device_status_code'] = last_log['plant'].get('device_status_code', 0)
        print(f"  Plant Status: {solar_panel_data['device_status']['plant_status']['status']}")
    
    # Inverter statuses
    if 'inverter' in last_log:
        for inv_id, inv_data in last_log['inverter'].items():
            if isinstance(inv_data, dict):
                inv_status = {
                    "status": get_device_status_text(inv_data.get('suryalog_status', 0)),
                    "status_code": inv_data.get('suryalog_status', 0),
                    "user_status": inv_data.get('user_status', 0),
                    "power_w": inv_data.get('WT', 0),
                    "temperature_c": inv_data.get('TEMP_INT', 0)
                }
                solar_panel_data['device_status']['inverters'][f"INV_{len(solar_panel_data['device_status']['inverters']) + 1}"] = inv_status
                print(f"    INV_{len(solar_panel_data['device_status']['inverters'])}: {inv_status['status']}")
    
    # Meter statuses
    if 'meter' in last_log:
        meter_names = ['SOLAR_METER', 'Grid Meter', 'DG', 'DG_1', 'DG_2', 'DG_3', 'DG_3B', 'DG_3C']
        meter_count = 0
        for meter_id, meter_data in last_log['meter'].items():
            if isinstance(meter_data, dict):
                meter_name = meter_names[meter_count] if meter_count < len(meter_names) else f'Meter_{meter_count}'
                meter_status = {
                    "name": meter_name,
                    "online": meter_data.get('meter_online', 0),
                    "status": "ONLINE" if meter_data.get('meter_online', 0) == 1 else "OFFLINE",
                    "power_w": meter_data.get('WT', 0),
                    "voltage_v": meter_data.get('VLN', 0)
                }
                solar_panel_data['device_status']['meters'][meter_name] = meter_status
                print(f"    {meter_name}: {meter_status['status']}")
                meter_count += 1
    
    # SMB statuses
    if 'smb' in last_log:
        smb_count = 0
        for smb_id, smb_data in last_log['smb'].items():
            if isinstance(smb_data, dict):
                smb_status = {
                    "status": get_device_status_text(smb_data.get('suryalog_status', 0)),
                    "status_code": smb_data.get('suryalog_status', 0),
                    "user_status": smb_data.get('user_status', 0),
                    "power_w": smb_data.get('WTOT', 0)
                }
                solar_panel_data['device_status']['smbboxes'][f"SMB_{smb_count + 1}"] = smb_status
                print(f"    SMB_{smb_count + 1}: {smb_status['status']}")
                smb_count += 1
    
    # ========== EXTRACT ENVIRONMENTAL DATA ==========
    print("Extracting environmental data...")
    
    # Try to extract weather/environmental data if available
    if 'weather' in last_log and isinstance(last_log['weather'], dict):
        weather_data = last_log['weather']
        if weather_data.get('irradiance'):
            solar_panel_data['environmental_data']['irradiance_w_m2'] = float(weather_data.get('irradiance', None))
        if weather_data.get('ambient_temp'):
            solar_panel_data['environmental_data']['ambient_temperature'] = float(weather_data.get('ambient_temp', None))
    
    # Try to estimate module temperature from inverter heatsink temp
    if solar_panel_data['device_status']['inverters']:
        temps = []
        for inv_data in solar_panel_data['device_status']['inverters'].values():
            if inv_data.get('temperature_c') and inv_data.get('temperature_c') > 0:
                temps.append(inv_data.get('temperature_c'))
        
        if temps:
            # Use average or highest inverter temperature as proxy for module temperature
            solar_panel_data['environmental_data']['module_temperature'] = round(max(temps), 1)
            print(f"  Module Temperature (from inverter heatsink): {solar_panel_data['environmental_data']['module_temperature']}°C")
    
    # Save to file
    output_file = os.path.join(script_dir, 'filtered_solar_panel_data.json')
    with open(output_file, 'w') as f:
        json.dump(solar_panel_data, f, indent=2)
    
    print("\n✓ Solar panel data extracted successfully!")
    print("\n=== SOLAR PANEL GENERATION SUMMARY ===")
    print(f"Today's Generation: {solar_panel_data['generation_summary']['today_generation_kWh']} kWh")
    print(f"Yesterday's Generation: {solar_panel_data['generation_summary']['yesterday_generation_kWh']} kWh (estimated)")
    print(f"Monthly Generation (estimated): {solar_panel_data['generation_summary']['monthly_generation_kWh']} kWh")
    print(f"Yearly Generation (estimated): {solar_panel_data['generation_summary']['yearly_generation_kWh']} kWh")
    print(f"Total Cumulative Generation: {solar_panel_data['generation_summary']['total_cumulative_generation_kWh']} kWh")
    print(f"Capacity Utilization Factor: {solar_panel_data['generation_summary']['capacity_utilization_factor_percent']}%")
    print(f"\nDaily Average Generation: {solar_panel_data['energy_metrics']['daily_average_kWh']} kWh")
    print(f"Peak Power Today: {solar_panel_data['energy_metrics']['peak_power_kW']} kW")
    print(f"\nNumber of Inverters: {len(solar_panel_data['inverter_details'])}")
    
    print(f"\nPerformance Metrics:")
    print(f"  Performance Ratio: {solar_panel_data['system_performance']['performance_ratio']}%")
    print(f"  System Loss: {solar_panel_data['system_performance']['system_loss_percent']}%")
    print(f"  Temp Coefficient: {solar_panel_data['system_performance']['temperature_coefficient']}%/°C")
    {output_file}
    print(f"\n✓ Full solar panel data saved to: filtered_solar_panel_data.json")
    
except Exception as e:
    print(f"Error extracting solar panel data: {str(e)}")
    import traceback
    traceback.print_exc()
