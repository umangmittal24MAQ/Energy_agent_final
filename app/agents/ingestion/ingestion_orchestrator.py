import subprocess
import schedule
import time
import os
from datetime import datetime
from pathlib import Path

# Get the directory of the current script
script_dir = Path(__file__).parent

# List of scripts to run in sequence - main pipeline (every 10 minutes)
scripts_to_run = [
    "scrape_to_sharepoint.py",
    "extract_30day_data.py",
    "extract_dashboard_data.py",
    "extract_solar_panel_data.py",
    "map_smb_to_grid.py",
    "build_grid_and_diesel_data.py"
]

# Scripts for 7-day data update (once daily at 9 PM)
scripts_7day_only = [
    "extract_30day_data.py",
    "filter_7day_values.py"
]

def run_script(script_name):
    """Run a single script and return success status"""
    script_path = script_dir / script_name
    
    if not script_path.exists():
        print(f"❌ ERROR: {script_name} not found at {script_path}")
        return False
    
    try:
        print(f"▶️  Running {script_name}...")
        
        # Set environment variable to unbuffer Python output
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        
        # Determine timeout based on script type
        # scrape.py needs more time due to browser automation
        timeout_seconds = 600 if script_name == "scrape.py" else 300
        
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env
        )
        
        # Print the output from the script
        if result.stdout:
            print(result.stdout)
        
        if result.returncode == 0:
            print(f"✅ {script_name} completed successfully")
            return True
        else:
            print(f"❌ {script_name} failed with exit code {result.returncode}")
            if result.stderr:
                print(f"   Error Output:\n{result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print(f"⏱️  TIMEOUT: {script_name} exceeded {timeout_seconds}-second limit")
        return False
    except Exception as e:
        print(f"❌ EXCEPTION in {script_name}: {str(e)}")
        return False

def run_ingestion_pipeline():
    """Run the complete ingestion pipeline"""
    print("\n" + "=" * 70)
    print(f"🚀 INGESTION PIPELINE STARTED - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    successful_scripts = []
    failed_scripts = []
    start_time = time.time()
    
    for script_name in scripts_to_run:
        try:
            if run_script(script_name):
                successful_scripts.append(script_name)
            else:
                failed_scripts.append(script_name)
        except Exception as e:
            print(f"❌ Unexpected error running {script_name}: {str(e)}")
            failed_scripts.append(script_name)
        
        # Small delay between scripts
        time.sleep(1)
    
    elapsed_time = time.time() - start_time
    
    # Print summary
    print("\n" + "=" * 70)
    print("📊 PIPELINE SUMMARY")
    print("=" * 70)
    print(f"✅ Successful: {len(successful_scripts)}/{len(scripts_to_run)}")
    if successful_scripts:
        for script in successful_scripts:
            print(f"   ✓ {script}")
    
    if failed_scripts:
        print(f"\n❌ Failed: {len(failed_scripts)}")
        for script in failed_scripts:
            print(f"   ✗ {script}")
    

    return len(failed_scripts) == 0

def run_7day_update():
    """Run the 7-day data update pipeline (extract and filter 7-day values)"""
    print("\n" + "=" * 70)
    print(f"📅 7-DAY DATA UPDATE PIPELINE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    successful_scripts = []
    failed_scripts = []
    start_time = time.time()
    
    for script_name in scripts_7day_only:
        try:
            if run_script(script_name):
                successful_scripts.append(script_name)
            else:
                failed_scripts.append(script_name)
        except Exception as e:
            print(f"❌ Unexpected error running {script_name}: {str(e)}")
            failed_scripts.append(script_name)
        
        # Small delay between scripts
        time.sleep(1)
    
    elapsed_time = time.time() - start_time
    
    # Print summary
    print("\n" + "=" * 70)
    print("📊 7-DAY UPDATE SUMMARY")
    print("=" * 70)
    print(f"✅ Successful: {len(successful_scripts)}/{len(scripts_7day_only)}")
    if successful_scripts:
        for script in successful_scripts:
            print(f"   ✓ {script}")
    
    if failed_scripts:
        print(f"\n❌ Failed: {len(failed_scripts)}")
        for script in failed_scripts:
            print(f"   ✗ {script}")
    
    print(f"\n⏱️  Total execution time: {elapsed_time:.2f} seconds")
    print(f"🏁 7-Day update completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70 + "\n")
    
    return len(failed_scripts) == 0

def schedule_dual_pipeline():
    """Schedule dual pipelines:
    1. Main pipeline every 10 minutes (scrape, extract, map, write)
    2. 7-day update once daily at 9 PM (extract 7-day data)
    """
    print("\n" + "=" * 70)
    print("📅 DUAL INGESTION PIPELINE SCHEDULER")
    print("=" * 70)
    print("\n🔄 MAIN PIPELINE (Every 10 minutes):")
    print(f"   Scripts: {len(scripts_to_run)}")
    for i, script in enumerate(scripts_to_run, 1):
        print(f"   {i}. {script}")
    
    print("\n📅 7-DAY UPDATE PIPELINE (Daily at 9:00 PM):")
    print(f"   Scripts: {len(scripts_7day_only)}")
    for i, script in enumerate(scripts_7day_only, 1):
        print(f"   {i}. {script}")
    
    print("\n" + "=" * 70)
    print(f"Scheduler started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Press Ctrl+C to stop the scheduler\n")
    
    # Schedule the main pipeline to run every 10 minutes
    schedule.every(10).minutes.do(run_ingestion_pipeline)
    
    # Schedule 7-day data update to run once daily at 9:00 PM (21:00)
    schedule.every().day.at("21:00").do(run_7day_update)
    
    # Run the main pipeline immediately on start
    print("Starting initial main pipeline run...")
    run_ingestion_pipeline()
    
    # Keep scheduler running
    try:
        while True:
            schedule.run_pending()
            time.sleep(5)  # Check every 5 seconds if a job needs to run
    except KeyboardInterrupt:
        print("\n\n⛔ Scheduler stopped by user")
        print(f"Scheduler stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

def run_once():
    """Run the pipeline once (for testing)"""
    return run_ingestion_pipeline()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        # Run pipeline once
        success = run_once()
        exit(0 if success else 1)
    else:
        # Start dual scheduler
        schedule_dual_pipeline()
