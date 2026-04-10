import json
import os
import sys
from datetime import datetime, timedelta

# Fix Unicode encoding on Windows
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Get the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load the filtered 7-day data
with open(os.path.join(script_dir, 'filtered_7day_data.json'), 'r') as f:
    data_7day = json.load(f)

print("=" * 60)
print("FILTERING 7-DAY DATA - EXTRACTING KEY FIELDS")
print("=" * 60)

# Extract daily timeline from 7-day data
daily_timeline = data_7day.get('daily_timeline', {})

# Build a logical calendar window: today-8 through today-1.
# Example: if today is 25 Mar, include 17-24 Mar.
today = datetime.now().date()
window_start = today - timedelta(days=8)
window_end = today - timedelta(days=1)

# Build all dates in the target window (newest first), so missing days are still shown.
window_dates = [
    (window_end - timedelta(days=offset)).isoformat()
    for offset in range((window_end - window_start).days + 1)
]

print(f"\n📅 Processing {len(window_dates)} days...\n")
print(f"Target Window: {window_start.isoformat()} to {window_end.isoformat()}\n")

# Build filtered data
filtered_data = {
    "extraction_info": {
        "source": "filtered_7day_data.json",
        "period": "Previous complete days (excluding today)",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "last_update": datetime.now().isoformat(),
        "extraction_date": datetime.now().strftime("%Y-%m-%d"),
        "fields_included": ["date", "time", "generation_wh", "start_value", "end_value"]
    },
    "daily_values": {}
}

total_records = 0

# Process each day in the target calendar window
for date_str in window_dates:
    day_data = daily_timeline.get(date_str, {})
    all_readings = day_data.get('all_readings', [])
    daily_records = []
    
    # Extract specified fields from each reading
    for reading in all_readings:
        try:
            record = {
                "date": reading.get('date'),
                "time": reading.get('time'),
                "generation_wh": reading.get('generation_wh'),
                "start_value": reading.get('start_value'),
                "end_value": reading.get('end_value')
            }
            daily_records.append(record)
            total_records += 1
        except Exception as e:
            continue
    
    filtered_data['daily_values'][date_str] = {
        "date_formatted": day_data.get('date', date_str),
        "records_count": len(daily_records),
        "records": daily_records
    }

    if daily_records:
        print(f"📅 {date_str}: {len(daily_records)} records")
        for record in daily_records:
            print(f"   ├─ {record['time']}: start={record['start_value']}, end={record['end_value']}, generation={record['generation_wh']} Wh")
    else:
        print(f"📅 {date_str}: 0 records")

# Save filtered output
output_file = os.path.join(script_dir, '7day_final.json')
with open(output_file, 'w') as f:
    json.dump(filtered_data, f, indent=2)

print("\n" + "=" * 60)
print("✓ FILTERING COMPLETE")
print("=" * 60)

print(f"\n📊 SUMMARY:")
print(f"  Total Records: {total_records}")
print(f"  Days Processed: {len(filtered_data['daily_values'])}")
print(f"  Output File: {output_file}\n")
