import os
import sys
from dotenv import load_dotenv

# 1. Load the .env file BEFORE anything else happens!
load_dotenv()

# Add the root directory to the python path so it can find the 'app' folder
sys.path.append(os.getcwd())

def run_manual_reminder_test():
    print("\n" + "="*50)
    print("MANUAL OPERATOR REMINDER TEST")
    print("="*50)

    try:
        from app.services.scheduler_service import check_grid_diesel_entry_exists
        from app.services.email_service import send_operator_reminder

        print("1. Checking SharePoint for today's Grid/Diesel data...")
        data_exists = check_grid_diesel_entry_exists()

        if data_exists:
            print("✅ STATUS: Data is ALREADY UPLOADED.")
            print("   Normally, the scheduler would cancel the email here.")
            print("   Forcing the reminder to send anyway for testing...\n")
        else:
            print("❌ STATUS: Data is MISSING.")
            print("   This is the correct condition. The scheduler would trigger.\n")

        print("2. Firing the send_operator_reminder() function...")
        result = send_operator_reminder()

        print("\n=== RESULTS ===")
        print(f"Status : {result.get('status')}")
        
        if result.get("notes"):
            print(f"Notes  : {result.get('notes')}")
        if result.get("error"):
            print(f"Error  : {result.get('error')}")

    except Exception as e:
        print(f"\n❌ CRASHED: {str(e)}")
        import traceback
        traceback.print_exc()

    print("="*50 + "\n")

if __name__ == "__main__":
    run_manual_reminder_test()