import os
import sys
from dotenv import load_dotenv

# 1. Load the .env file so it has access to SharePoint and Gmail locally
load_dotenv()

# Add the root directory to the python path so it can find the 'app' folder
sys.path.append(os.getcwd())

def run_master_override():
    print("\n" + "="*60)
    print("🚀 MANUAL MASTER TRIGGER: SMART DISPATCH")
    print("="*60)

    try:
        # Import the exact functions your automated clock uses
        from app.services.scheduler_service import check_grid_diesel_entry_exists, _run_master_data_engine
        from app.services.email_service import send_operator_reminder, send_daily_report

        print("1. Checking SharePoint for today's Grid/Diesel data...")
        data_exists = check_grid_diesel_entry_exists()

        if not data_exists:
            print("❌ STATUS: Data is MISSING.")
            print("   Action: Dispatching the 'Action Required' Reminder to Operator...\n")
            
            result = send_operator_reminder()
            
            print(f"   Result : {result.get('status')}")
            print(f"   Details: {result.get('notes') or result.get('error')}")

        else:
            print("✅ STATUS: Data is ALREADY UPLOADED.")
            print("   Action: Running Master Data Engine & Sending Final Report...\n")
            
            print("   -> Merging Data from Grid & Solar files...")
            engine_result = _run_master_data_engine()
            
            if engine_result.get("status") == "Success":
                print("   -> Dispatching Final Report to Management Team...")
                
                # We flag this as a 'manual_override' so it shows up in your Azure logs if run on the server
                result = send_daily_report(trigger_source="manual_override", is_missing_data=False)
                
                print(f"\n   === REPORT RESULTS ===")
                print(f"   Status     : {result.get('status')}")
                print(f"   Recipients : {result.get('recipients', 'N/A')}")
                print(f"   Attachment : {result.get('attachment', 'N/A')}")
                if result.get("error"):
                    print(f"   Error      : {result.get('error')}")
            else:
                print(f"\n   ❌ CRITICAL ERROR: Master Engine failed to merge data.")
                print(f"   Details: {engine_result.get('error')}")

    except Exception as e:
        print(f"\n❌ SCRIPT CRASHED: {str(e)}")
        import traceback
        traceback.print_exc()

    print("="*60 + "\n")

if __name__ == "__main__":
    run_master_override()