"""
EnergyAgent — Targeted Reminder Flow Test (Real Environment)
=====================================================
Tests ONLY the send_operator_reminder function for all 3 escalation levels.
Uses your actual environment variables without overriding them.
"""

import os
import sys
import time
import importlib
from datetime import datetime
from zoneinfo import ZoneInfo
import dotenv

# This loads your actual .env file
dotenv.load_dotenv()

# ── colour helpers ────────────────────────────────────────────────────────────
BOLD="\033[1m"; GREEN="\033[92m"; RED="\033[91m"; CYAN="\033[96m"
YELLOW="\033[93m"; RESET="\033[0m"
def ok(m):     print(f"  {GREEN}✓{RESET}  {m}")
def fail(m):   print(f"  {RED}✗{RESET}  {m}")
def info(m):   print(f"  {CYAN}→{RESET}  {m}")
def header(m): print(f"\n{BOLD}{m}{RESET}")
def sep():     print(f"  {'─'*60}")

# ── config ────────────────────────────────────────────────────────────────────
TEST_RECIPIENT = os.getenv("OPERATOR_EMAIL", "")
TEST_CC = os.getenv("CC_EMAIL", "")

if not TEST_RECIPIENT:
    print(f"{RED}Set OPERATOR_EMAIL in your .env (or system vars) before running.{RESET}")
    sys.exit(1)

# Ensure the app module can be found
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

try:
    from app.services import email_service as _es
except ImportError as e:
    print(f"{RED}Could not import email_service. Are you in the project root? Error: {e}{RESET}")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# TEST — send_operator_reminder (Levels 1, 2, and 3)
# ══════════════════════════════════════════════════════════════════════════════
header("TESTING REMINDER SERVICE (1 through 3)")
info(f"Target Inbox (TO): {TEST_RECIPIENT}")
info(f"Target Inbox (CC): {TEST_CC if TEST_CC else 'None configured'}")
sep()

results = {}

# We loop through reminders 1, 2, and 3
for reminder_num in range(1, 4):
    print(f"\n{BOLD}Triggering Reminder {reminder_num}/3...{RESET}")
    
    try:
        # Reloading ensures it picks up the current env vars accurately
        importlib.reload(_es)

        # Call the specific reminder number
        result = _es.send_operator_reminder(
            reminder_number=reminder_num, 
            total_reminders=3, 
            deadline_str="10:30 AM"
        )

        if result and result.get("status") == "Success":
            ok(f"Status: {result['status']}")
            
            if reminder_num == 1:
                info("Check Inbox: Should be GREEN formatting.")
            elif reminder_num == 2:
                info("Check Inbox: Should be ORANGE formatting.")
            elif reminder_num == 3:
                info("Check Inbox: Should be RED formatting + Final Warning text.")
                
            results[f"Reminder {reminder_num}"] = "PASS"
        else:
            fail(f"Returned: {result}")
            results[f"Reminder {reminder_num}"] = "FAIL"

    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
        results[f"Reminder {reminder_num}"] = "FAIL"
        
    # Wait 3 seconds between sending so they don't arrive out of order
    if reminder_num < 3:
        time.sleep(3)

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
header("Test Results")
print()
all_pass = True
for fn, status in results.items():
    icon = f"{GREEN}PASS{RESET}" if status == "PASS" else f"{RED}FAIL{RESET}"
    print(f"  {icon}  {fn}")
    if status != "PASS":
        all_pass = False

print()
if all_pass:
    print(f"  {GREEN}{BOLD}All 3 reminders triggered successfully using your live .env variables!{RESET}")
else:
    print(f"  {YELLOW}Some reminders failed to send. Check the logs above.{RESET}")
print()