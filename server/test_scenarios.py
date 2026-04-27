"""
test_scenarios.py
Interactive CLI to test all Happy and Exception paths for the Energy Dashboard.
"""
import sys
import logging
import io # 🚀 ADD THIS
from pathlib import Path
from dotenv import load_dotenv

# 🚀 ADD THIS BLOCK TO FIX WINDOWS EMOJI CRASHES
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# Setup paths and load environment
server_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(server_dir))
load_dotenv(server_dir / ".env")

# Import the actual backend functions
from app.services.scheduler_service import (
    _run_operator_reminder_cycle,
    run_daily_report_automation,
    _daily_report_tracker,
    _run_solar_scraper
)
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="\n%(message)s")
logger = logging.getLogger(__name__)

def reset_tracker():
    """Clears the lock so we can test multiple scenarios without restarting."""
    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    if today_str in _daily_report_tracker:
        del _daily_report_tracker[today_str]
    logger.info("🔄 Internal Tracker Reset. Ready for new scenario.")

def wait_for_user(prompt):
    input(f"\n⏸️  ACTION REQUIRED: {prompt}\n   (Press ENTER when ready...)")

def main():
    while True:
        reset_tracker()
        print("\n" + "="*60)
        print("⚡ ENERGY DASHBOARD - AUTOMATION TEST HARNESS ⚡")
        print("="*60)
        print("--- HAPPY PATHS ---")
        print("1. Data updated BEFORE 9:00 -> Mail sent at 9:00")
        print("2. Reminder at 9:00 -> Data updated -> Mail sent at 9:30")
        print("3. Reminders at 9, 9:30, 10 -> Data updated -> Mail sent at 10:30")
        print("\n--- EXCEPTION PATHS ---")
        print("4. 3 Reminders -> NO Data -> Final fallback mail at 10:30")
        print("5. Half Updated (Grid missing, or Status NOT 'Done')")
        print("6. Race Condition (Updating Excel while Scraper/Engine is running)")
        print("7. Broken Excel (Data not fetched due to broken sheet)")
        print("8. Test Solar Scraper Lock/Failure")
        print("0. EXIT")
        print("="*60)
        
        choice = input("Select a scenario to run (0-8): ")
        
        if choice == '1':
            wait_for_user("Go to SharePoint 'grid_and_diesel' Excel. Ensure TODAY'S data is fully filled and Status='Done'.")
            print("\n🕒 Simulating 9:00 AM Cycle...")
            _run_operator_reminder_cycle()
            
        elif choice == '2':
            wait_for_user("Go to SharePoint 'grid_and_diesel'. DELETE today's data or remove the 'Done' status.")
            print("\n🕒 Simulating 9:00 AM Cycle...")
            _run_operator_reminder_cycle() # Should send Reminder 1
            
            wait_for_user("Now, go FILL IN today's data and set Status='Done'.")
            print("\n🕒 Simulating 9:30 AM Cycle...")
            _run_operator_reminder_cycle() # Should send Final Report early
            
        elif choice == '3':
            wait_for_user("Go to SharePoint 'grid_and_diesel'. DELETE today's data or remove 'Done' status.")
            print("\n🕒 Simulating 9:00 AM Cycle...")
            _run_operator_reminder_cycle() # Reminder 1
            print("\n🕒 Simulating 9:30 AM Cycle...")
            _run_operator_reminder_cycle() # Reminder 2
            print("\n🕒 Simulating 10:00 AM Cycle...")
            _run_operator_reminder_cycle() # Reminder 3
            
            wait_for_user("Now, go FILL IN today's data and set Status='Done'.")
            print("\n🕒 Simulating 10:30 AM DEADLINE...")
            run_daily_report_automation(trigger_source="scheduler") # Should send Final Report
            
        elif choice == '4':
            wait_for_user("Go to SharePoint 'grid_and_diesel'. DELETE today's data.")
            print("\n🕒 Simulating 9:00 AM Cycle...")
            _run_operator_reminder_cycle()
            print("\n🕒 Simulating 9:30 AM Cycle...")
            _run_operator_reminder_cycle()
            print("\n🕒 Simulating 10:00 AM Cycle...")
            _run_operator_reminder_cycle()
            
            print("\n🕒 Simulating 10:30 AM DEADLINE (No Data Provided)...")
            run_daily_report_automation(trigger_source="scheduler") # Should send Fallback Mail
            
        elif choice == '5':
            wait_for_user("Go to SharePoint. Fill in Grid data, but leave Diesel blank, OR type 'Pending' in Status.")
            print("\n🕒 Simulating 9:00 AM Cycle...")
            _run_operator_reminder_cycle() # Should REJECT it and send a reminder
            
        elif choice == '6':
            print("\n⚠️ RACE CONDITION TEST")
            wait_for_user("Open 'UnifiedSolarData.xlsx' in the Desktop Excel App (or open in browser and click 'Editing'). Start typing in a cell and DO NOT hit enter. This puts a Microsoft Graph Lock on the file.")
            print("\n🕒 Forcing Scraper to run while file is locked...")
            _run_solar_scraper()
            print("\n> Check your 'solar_offline_cache.json' file locally. The data should have been saved there safely!")
            
            wait_for_user("Now, hit Enter in Excel to finish editing and close the file to unlock it.")
            print("\n🕒 Simulating next 30-min scraper cycle...")
            _run_solar_scraper()
            print("\n> Check SharePoint. Both the cached row and the new row should now be uploaded!")

        elif choice == '7':
            print("\n⚠️ BROKEN EXCEL TEST")
            wait_for_user("Go to SharePoint. Rename the 'grid_and_diesel' sheet to 'broken_tab', OR delete the 'Date' column header.")
            print("\n🕒 Simulating 10:30 AM DEADLINE...")
            run_daily_report_automation(trigger_source="scheduler")
            print("\n> The script should survive, log a 'CRITICAL: No date column' error, and send a fallback email without crashing the server.")

        elif choice == '8':
            print("\n🕒 Forcing Solar Scraper...")
            _run_solar_scraper()
            
        elif choice == '0':
            print("Exiting Test Harness...")
            break
        else:
            print("Invalid choice.")

if __name__ == "__main__":
    main()