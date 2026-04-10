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

monthly_data = {
    "data_extraction_info": {
        "last_update": datetime.now().isoformat(),
        "extraction_date": datetime.now().strftime("%Y-%m-%d"),
        "period": "Last Days (auto-determined)",
        "data_source": "solar_meter only from /livebar/gen_info API",
        "data_granularity": "All readings (hourly/timestamp-level data)"
    },
    "period_summary": {
        "total_generation_30days_kWh": 0,
        "average_daily_generation_kWh": 0,
        "peak_daily_generation_kWh": 0,
        "minimum_daily_generation_kWh": 0,
        "days_with_data": 0,
        "zero_generation_days": 0,
        "total_period_days": 0
    },
    "weekly_breakdown": {},
    "daily_timeline": {},
    "daily_performance_metrics": {
        "efficiency_by_day": {},
        "generation_trend": "calculating..."
    },
    "plant_info": {}
}

try:
    # Get plant info
    plant_info = api_data[0]['data']['plantInfo']
    dc_capacity = float(plant_info.get('dc_size', 598.6))
    ac_capacity = float(plant_info.get('ac_size', 500.0))
    
    # Get the latest timestamp from the server
    latest_timestamp = plant_info.get('plant_create_time', int(time.time()))
    latest_date = datetime.fromtimestamp(latest_timestamp).date()
    
    print("=" * 60)
    print("EXTRACTING 7-DAY SOLAR GENERATION DATA")
    print("=" * 60)
    
    print(f"\n📅 Latest Server Date: {latest_date}")
    print(f"✓ Processing last 7 days of data...\n")
    
    # Extract solar_meter daily report data
    daily_generation = defaultdict(float)
    all_readings = defaultdict(list)  # Store ALL readings per day
    timestamp_to_date = {}
    
    print("Scanning SOLAR_METER report data for daily readings...")
    
    # Navigate to SOLAR_METER in plantInfo
    meter_data_found = False
    if 'meter' in plant_info and isinstance(plant_info['meter'], dict):
        for meter_id, meter_info in plant_info['meter'].items():
            if isinstance(meter_info, dict):
                meter_name = meter_info.get('name', '')
                if meter_name == 'SOLAR_METER' and 'report' in meter_info:
                    print(f"  ✓ Found SOLAR_METER: {meter_id}")
                    
                    # Get report data
                    reports = meter_info.get('report', {})
                    if isinstance(reports, dict):
                        for report_id, report_data in reports.items():
                            if isinstance(report_data, dict) and 'report' in report_data:
                                report_array = report_data['report']
                                
                                if isinstance(report_array, list):
                                    print(f"  ✓ Processing {len(report_array)} daily records from report...\n")
                                    
                                    for report_entry in report_array:
                                        try:
                                            if isinstance(report_entry, dict):
                                                start_time = int(report_entry.get('start_time', 0))
                                                end_time = int(report_entry.get('end_time', 0))
                                                
                                                if start_time > 0:
                                                    reading_date = datetime.fromtimestamp(start_time).date()
                                                    reading_time = datetime.fromtimestamp(start_time)
                                                    
                                                    # Extract generation value (in Wh, convert to kWh)
                                                    gen_value = float(report_entry.get('value', 0)) / 1000
                                                    
                                                    # Store all report entry data
                                                    reading_entry = {
                                                        "timestamp": reading_time.isoformat(),
                                                        "unix_timestamp": start_time,
                                                        "start_time": start_time,
                                                        "end_time": end_time,
                                                        "date": str(reading_date),
                                                        "time": reading_time.strftime("%H:%M:%S"),
                                                        "generation_wh": float(report_entry.get('value', 0)),
                                                        "generation_kwh": gen_value,
                                                        "start_value": float(report_entry.get('start_value', 0)),
                                                        "end_value": float(report_entry.get('end_value', 0)),
                                                        "duration_seconds": int(report_entry.get('duration', 0)),
                                                        "report_entry": report_entry
                                                    }
                                                    
                                                    all_readings[str(reading_date)].append(reading_entry)
                                                    # Keep the highest cumulative generation reading for each date.
                                                    daily_generation[str(reading_date)] = max(
                                                        daily_generation.get(str(reading_date), 0),
                                                        gen_value,
                                                    )
                                        except Exception as e:
                                            continue
                                    
                                    meter_data_found = True
    
    if meter_data_found:
        print(f"  ✓ Found {len(all_readings)} days with solar_meter readings")
        total_readings = sum(len(readings) for readings in all_readings.values())
        print(f"  ✓ Total readings collected: {total_readings}\n")
    else:
        print("  ⚠️  SOLAR_METER or report data not found in plantInfo\n")
    
    # ========== BUILD TIMELINE WITH FALLBACK LOGIC ==========
    print("Extracting last 7 days of data...")
    
    # Get all unique dates from collected readings
    available_dates = sorted([datetime.strptime(d, '%Y-%m-%d').date() for d in all_readings.keys()])
    
    if available_dates:
        latest_available_date = available_dates[-1]
        earliest_available_date = available_dates[0]
        print(f"  ✓ Data available from {earliest_available_date} to {latest_available_date}")
    else:
        latest_available_date = latest_date
        earliest_available_date = latest_date
        print(f"  ⚠️  No readings found in available data")
    
    # Determine the appropriate time period based on available data
    period_days = 7  # Extract only last 7 days
    dates_with_data = []
    
    for i in range(period_days):
        check_date = latest_available_date - timedelta(days=i)
        date_str = str(check_date)
        generation_kwh = daily_generation.get(date_str, 0)
        dates_with_data.append({
            'date': date_str,
            'generation': generation_kwh,
            'days_ago': i
        })
    
    selected_period = period_days
    print(f"  ✓ Using {period_days}-day period\n")
    
    # Update metadata with actual period used
    monthly_data["data_extraction_info"]["period"] = f"Last {selected_period} Days"
    monthly_data["period_summary"]["total_period_days"] = selected_period
    
    # Build timeline
    for i, date_dict in enumerate(dates_with_data):
        date_str = date_dict['date']
        check_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        generation_kwh = date_dict['generation']
        
        # Include all readings for this day
        day_readings = all_readings.get(date_str, [])
        
        monthly_data['daily_timeline'][date_str] = {
            "date": check_date.strftime("%A, %B %d, %Y"),
            "generation_kWh": round(generation_kwh, 2),
            "days_ago": date_dict['days_ago'],
            "efficiency_percent": None,  # Will calculate below
            "total_readings": len(day_readings),
            "all_readings": day_readings  # Store ALL readings for the day
        }
    
    # Reverse to get oldest to newest
    dates_with_data.reverse()
    
    # ========== CALCULATE SUMMARY METRICS ==========
    print("Calculating performance metrics...")
    
    non_zero_days = [d for d in dates_with_data if d['generation'] > 0]
    all_generations = [d['generation'] for d in dates_with_data]
    
    if non_zero_days:
        total_gen = sum(d['generation'] for d in non_zero_days)
        peak_gen = max(d['generation'] for d in non_zero_days)
        min_gen = min(d['generation'] for d in non_zero_days)
        avg_gen = total_gen / len(non_zero_days)
        
        monthly_data['period_summary']['total_generation_30days_kWh'] = round(total_gen, 2)
        monthly_data['period_summary']['average_daily_generation_kWh'] = round(avg_gen, 2)
        monthly_data['period_summary']['peak_daily_generation_kWh'] = round(peak_gen, 2)
        monthly_data['period_summary']['minimum_daily_generation_kWh'] = round(min_gen, 2)
        monthly_data['period_summary']['days_with_data'] = len(non_zero_days)
        monthly_data['period_summary']['zero_generation_days'] = selected_period - len(non_zero_days)
        
        print(f"  ✓ Total 7-day Generation: {total_gen:.2f} kWh")
        print(f"  ✓ Best Day: {peak_gen:.2f} kWh")
        print(f"  ✓ Average per Day: {avg_gen:.2f} kWh")
        print(f"  ✓ Days with Data: {len(non_zero_days)}/{selected_period}\n")
    
    # ========== CALCULATE EFFICIENCY ==========
    print("Calculating daily efficiency...")
    
    # Efficiency = (Actual Generation) / (Theoretical Max) × 100
    # Theoretical Max per day = AC Capacity × Peak Sun Hours (assume 5 hours)
    theoretical_max_daily = ac_capacity * 5  # kWh
    
    for date_str, timeline_data in monthly_data['daily_timeline'].items():
        gen = timeline_data['generation_kWh']
        if gen > 0:
            efficiency = (gen / theoretical_max_daily) * 100
            timeline_data['efficiency_percent'] = round(efficiency, 1)
        else:
            timeline_data['efficiency_percent'] = 0
    
    # ========== WEEKLY BREAKDOWN ==========
    print("Building weekly breakdown...")
    
    # Clear previous weeks and rebuild based on actual period
    monthly_data["weekly_breakdown"] = {}
    max_weeks = (selected_period // 7) + 1
    
    for i, date_dict in enumerate(dates_with_data):
        week_num = (i // 7) + 1
        week_key = f"week_{week_num}"
        
        if week_key not in monthly_data["weekly_breakdown"]:
            monthly_data["weekly_breakdown"][week_key] = {"generation_kWh": 0, "days": {}}
        
        if week_num <= max_weeks:
            date_str = date_dict['date']
            gen = date_dict['generation']
            
            monthly_data["weekly_breakdown"][week_key]['days'][date_str] = {
                "generation_kWh": round(gen, 2),
                "efficiency_percent": round(
                    (gen / theoretical_max_daily * 100) if gen > 0 else 0, 1
                )
            }
            monthly_data["weekly_breakdown"][week_key]['generation_kWh'] += gen
    
    # Round weekly totals
    for week_key in monthly_data["weekly_breakdown"]:
        monthly_data["weekly_breakdown"][week_key]['generation_kWh'] = round(
            monthly_data["weekly_breakdown"][week_key]['generation_kWh'], 2
        )
    
    # ========== TREND ANALYSIS ==========
    print("Analyzing generation trend...")
    
    if len(non_zero_days) >= 5:
        first_week_avg = sum(d['generation'] for d in non_zero_days[:7]) / min(7, len(non_zero_days))
        last_week_avg = sum(d['generation'] for d in non_zero_days[-7:]) / min(7, len(non_zero_days))
        
        if first_week_avg > 0:
            trend_percent = ((last_week_avg - first_week_avg) / first_week_avg) * 100
            if trend_percent > 5:
                trend = f"📈 Uptrend (+{trend_percent:.1f}%)"
            elif trend_percent < -5:
                trend = f"📉 Downtrend ({trend_percent:.1f}%)"
            else:
                trend = f"➡️ Stable (±{abs(trend_percent):.1f}%)"
        else:
            trend = "Insufficient data"
    else:
        trend = "Insufficient data"
    
    monthly_data['daily_performance_metrics']['generation_trend'] = trend
    
    # ========== ADD PLANT INFO ==========
    print("Adding plant metadata...")
    
    monthly_data['plant_info'] = {
        "plant_name": plant_info.get('plant_name'),
        "plant_id": plant_info.get('plant_id'),
        "device_model": plant_info.get('device_model'),
        "device_sw_version": '.'.join(plant_info.get('device_sw_version', [])),
        "device_hw_version": '.'.join(plant_info.get('device_hw_version', [])),
        "dc_capacity_kwp": dc_capacity,
        "ac_capacity_kw": ac_capacity,
        "theoretical_max_daily_kwh": round(theoretical_max_daily, 2),
        "number_of_inverters": plant_info.get('inverterNos', 0),
        "installation_date": datetime.fromtimestamp(
            float(plant_info.get('plant_installed', 0))
        ).strftime("%Y-%m-%d"),
        "latitude": plant_info.get('latitude'),
        "longitude": plant_info.get('longitude'),
        "timezone": plant_info.get('time_zone'),
        "co2_mitigation_factor": float(plant_info.get('co2_factor', 0.825))
    }
    
    # ========== SAVE OUTPUT ==========
    output_file = os.path.join(script_dir, 'filtered_7day_data.json')
    with open(output_file, 'w') as f:
        json.dump(monthly_data, f, indent=2)
    
    print("\n" + "=" * 60)
    print("✓ SOLAR_METER 7-DAY DATA EXTRACTION COMPLETE")
    print("=" * 60)
    
    print("\n📊 SUMMARY STATISTICS:")
    if dates_with_data:
        print(f"  Period: Last {selected_period} Days (from {dates_with_data[0]['date']} to {dates_with_data[-1]['date']})")
        print(f"  Data Source: SOLAR_METER from plantInfo (daily report)")
        print(f"  Data Granularity: ALL readings (not aggregated)")
    else:
        print(f"  Period: Last {selected_period} Days")
        print(f"  Data Source: SOLAR_METER from plantInfo (daily report)")
        print(f"  Data Granularity: ALL readings (not aggregated)")
    print(f"  Total Generation: {monthly_data['period_summary']['total_generation_30days_kWh']} kWh (7-day period)")
    print(f"  Daily Average: {monthly_data['period_summary']['average_daily_generation_kWh']} kWh")
    print(f"  Peak Day: {monthly_data['period_summary']['peak_daily_generation_kWh']} kWh")
    print(f"  Minimum Day: {monthly_data['period_summary']['minimum_daily_generation_kWh']} kWh")
    print(f"  Days with Generation: {monthly_data['period_summary']['days_with_data']}/{selected_period}")
    print(f"  Zero Generation Days: {monthly_data['period_summary']['zero_generation_days']}")
    print(f"  Total Data Points: {sum(d['total_readings'] for d in monthly_data['daily_timeline'].values())}")
    
    print(f"\n📈 WEEKLY BREAKDOWN:")
    for week_key, week_data in monthly_data['weekly_breakdown'].items():
        if week_data['days']:
            gen = week_data['generation_kWh']
            days_count = len(week_data['days'])
            avg = gen / days_count if days_count > 0 else 0
            print(f"  {week_key.upper()}: {gen} kWh ({days_count} days, Avg: {avg:.2f} kWh/day)")
    
    print(f"\n{monthly_data['daily_performance_metrics']['generation_trend']}")
    
    print(f"\n💾 Full data saved to: {output_file}")
    
except Exception as e:
    print(f"\n❌ Error extracting 30-day data: {str(e)}")
    import traceback
    traceback.print_exc()
