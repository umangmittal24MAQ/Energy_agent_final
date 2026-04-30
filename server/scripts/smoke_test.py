"""
Pre-deployment smoke test for Energy Dashboard backend.
Run this from the repo root before every Azure deployment:

    python scripts/smoke_test.py

Exit code 0 = all checks passed. Exit code 1 = one or more checks failed.
"""
import sys
import os
from pathlib import Path

# ── Bootstrap: load .env and put the repo root on sys.path ──────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=REPO_ROOT / ".env")
except ImportError:
    print("[WARN] python-dotenv not installed — relying on shell environment only.")

# ── Helpers ──────────────────────────────────────────────────────────────────
PASS  = "\033[32mPASS\033[0m"
FAIL  = "\033[31mFAIL\033[0m"
SKIP  = "\033[33mSKIP\033[0m"

results: list[tuple[str, str, str]] = []   # (check_name, status, detail)

def record(name: str, passed: bool, detail: str = "") -> None:
    status = PASS if passed else FAIL
    results.append((name, status, detail))
    label = "PASS" if passed else "FAIL"
    print(f"  [{label}] {name}" + (f" — {detail}" if detail else ""))


# ── Check 1: Required env vars are present ───────────────────────────────────
print("\n── Check 1: Required environment variables ──")
required_vars = [
    "SHAREPOINT_TENANT_ID",
    "SHAREPOINT_CLIENT_ID",
    "SHAREPOINT_CLIENT_SECRET",
    "SHAREPOINT_SITE_URL",
    "SHAREPOINT_MASTER_DATA_DRIVE_ID",
    "SHAREPOINT_MASTER_DATA_FILE_NAME",
    "SHAREPOINT_GRID_DIESEL_DRIVE_ID",
    "SHAREPOINT_GRID_DIESEL_FILE_NAME",
    "EMAIL_FROM",
    "EMAIL_PASSWORD",
    "SMTP_SERVER",
]
missing = [v for v in required_vars if not os.getenv(v, "").strip()]
record("env_vars", not missing,
       f"missing: {', '.join(missing)}" if missing else "all present")


# ── Check 2: SharePoint authentication ───────────────────────────────────────
print("\n── Check 2: SharePoint authentication ──")
sp_service = None
try:
    from app.services.sharepoint_data_service import get_service
    sp_service = get_service()
    token_ok = sp_service.authenticated
    record("sharepoint_auth", token_ok,
           "token acquired" if token_ok else "authentication failed — check SHAREPOINT_* vars")
except Exception as e:
    record("sharepoint_auth", False, str(e))


# ── Check 3: Fetch master_data (≤5 rows) ─────────────────────────────────────
print("\n── Check 3: Fetch master_data ──")
master_df = None
if sp_service and sp_service.authenticated:
    try:
        master_df = sp_service.fetch_sheet_data("master_data")
        ok = master_df is not None and not master_df.empty
        detail = f"{len(master_df)} rows, {len(master_df.columns)} columns" if ok else "returned None or empty"
        record("fetch_master_data", ok, detail)
    except Exception as e:
        record("fetch_master_data", False, str(e))
else:
    print(f"  [{SKIP}] fetch_master_data — skipped (auth failed)")


# ── Check 4: _coerce_for_column() unit test ───────────────────────────────────
print("\n── Check 4: _coerce_for_column() logic ──")
try:
    from app.services.email_service import _coerce_for_column
    import pandas as pd

    df_float = pd.DataFrame({"val": [1.0, 2.0]})
    result_float = _coerce_for_column(df_float, "val", 99.9)
    float_ok = isinstance(result_float, float) and result_float == 99.9

    df_str = pd.DataFrame({"val": ["a", "b"]})
    result_str = _coerce_for_column(df_str, "val", 99.9)
    str_ok = isinstance(result_str, str)

    record("coerce_for_column_float", float_ok,
           f"returned {result_float!r}" if not float_ok else "float → float")
    record("coerce_for_column_str",   str_ok,
           f"returned {result_str!r}"  if not str_ok  else "float → str for string column")
except Exception as e:
    record("coerce_for_column", False, str(e))


# ── Check 5: check_grid_diesel_entry_exists() ────────────────────────────────
print("\n── Check 5: check_grid_diesel_entry_exists() ──")
if sp_service and sp_service.authenticated:
    try:
        from app.services.scheduler_service import check_grid_diesel_entry_exists
        result = check_grid_diesel_entry_exists()
        # Any bool response (True or False) means the function ran without crashing.
        record("grid_diesel_check", True,
               f"returned {result} (True = today's data present, False = missing/incomplete)")
    except Exception as e:
        record("grid_diesel_check", False, str(e))
else:
    print(f"  [{SKIP}] grid_diesel_check — skipped (auth failed)")


# ── Summary ──────────────────────────────────────────────────────────────────
print("\n── Summary ──────────────────────────────────────────────────────────")
failures = [r for r in results if "FAIL" in r[1]]
skips    = len(results) - len([r for r in results if r[1] in (PASS, FAIL)])

print(f"  Total checks : {len(results)}")
print(f"  Passed       : {len(results) - len(failures)}")
print(f"  Failed       : {len(failures)}")

if failures:
    print("\nFailed checks:")
    for name, _, detail in failures:
        print(f"  ✗ {name}: {detail}")
    print("\n\033[31mSMOKE TEST FAILED — do not deploy.\033[0m\n")
    sys.exit(1)
else:
    print("\n\033[32mAll checks passed — safe to deploy.\033[0m\n")
    sys.exit(0)