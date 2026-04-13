import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# --- 0. Windows Emoji Fix ---
# Forces the Windows terminal to support UTF-8 emojis without crashing
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# --- 1. AGGRESSIVELY LOAD .ENV FILE ---
env_path = Path(__file__).resolve().parent / ".env"

if env_path.exists():
    print(f"✅ Found .env file at {env_path}")
    load_dotenv(dotenv_path=env_path, override=True)
else:
    print(f"❌ CRITICAL ERROR: Could not find .env file at {env_path}")
    print("   Did Windows accidentally save it as '.env.txt'?")
    sys.exit(1)

# --- 2. NOW IMPORT THE APP SERVICES ---
# It is critical that these imports happen AFTER load_dotenv()
from app.services.email_service import send_operator_reminder
from app.services.scheduler_service import run_daily_report_automation, _run_operator_reminder_cycle

# --- 3. DEFINE THE TEST SUITE ---
def run_test_suite():
    print("\n--- 📬 Starting Mail System Tests ---\n")

    # Test 1: Direct Email Delivery (Tests SMTP credentials)
    print("[1/3] Testing Reminder Mail (Direct)...")
    reminder_result = send_operator_reminder()
    print(f"Result: {reminder_result}\n")

    # Test 2: The 10:00 AM Logic (Check Excel -> Remind if missing)
    print("[2/3] Testing Operator Reminder Logic...")
    _run_operator_reminder_cycle()
    print("\n")

    # Test 3: The 10:30 AM Logic (Master Engine -> Final Report)
    print("[3/3] Testing Full Daily Report Automation...")
    automation_result = run_daily_report_automation(trigger_source="manual_test_script")
    
    # Clean up the output so it's easy to read
    if automation_result and "daily_report" in automation_result:
        print(f"Result: {automation_result['daily_report']}")
    else:
        print(f"Result: {automation_result}")
        
    print("\n✅ Test Suite Completed.")

if __name__ == "__main__":
    run_test_suite()