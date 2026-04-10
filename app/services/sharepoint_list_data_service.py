"""
sharepoint_list_data_service.py  (MIGRATED VERSION)
====================================================
Drop-in replacement for the original service.
Now references the three canonical SharePoint Lists:
  - Grid_Diesel_List
  - Unified_Solar_List
  - Master_Data_List

All public method signatures are preserved so existing callers (loader.py,
scheduler_service.py, routes/) work without changes.
"""

import logging
import re
from typing import Dict, List, Any, Optional
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import os

from .sharepoint_auth import SharePointAuthManager, load_auth_config_from_env

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ── List name constants (set via env or defaults) ──────────────────────────────
GRID_DIESEL_LIST_NAME    = os.getenv("GRID_DIESEL_LIST_NAME",   "Grid_Diesel_List")
UNIFIED_SOLAR_LIST_NAME  = os.getenv("UNIFIED_SOLAR_LIST_NAME", "Unified_Solar_List")
MASTER_DATA_LIST_NAME    = os.getenv("MASTER_DATA_LIST_NAME",   "Master_Data_List")

# Legacy alias support (Ops_Manual_Entry → Grid_Diesel_List)
OPS_MANUAL_LIST_NAME     = os.getenv("SHAREPOINT_OPS_MANUAL_LIST_NAME", GRID_DIESEL_LIST_NAME)
SOLAR_MASTER_LIST_NAME   = os.getenv("SHAREPOINT_UNIFIED_MASTER_LIST_NAME", MASTER_DATA_LIST_NAME)

DEFAULT_LIST_CONFIG = {
    "solar_data":           {"list_name": UNIFIED_SOLAR_LIST_NAME, "date_field": "Date"},
    "grid_data":            {"list_name": GRID_DIESEL_LIST_NAME,   "date_field": "Date"},
    "diesel_data":          {"list_name": GRID_DIESEL_LIST_NAME,   "date_field": "Date"},
    "ops_manual":           {"list_name": OPS_MANUAL_LIST_NAME,    "date_field": "Date"},
    "solar_master_unified": {"list_name": MASTER_DATA_LIST_NAME,   "date_field": "Date"},
}


class SharePointListDataService:
    """Read/write SharePoint Online lists using Microsoft Graph API."""

    def __init__(self, auth_manager=None, site_id=None):
        if auth_manager:
            self.auth_manager = auth_manager
        else:
            config = load_auth_config_from_env()
            self.auth_manager = SharePointAuthManager(config)

        self.authenticated = self.auth_manager.get_access_token() is not None
        self.last_error: Optional[str] = None
        self.graph_base_url = "https://graph.microsoft.com/v1.0"
        self.site_id = site_id or os.getenv("SHAREPOINT_SITE_ID", "")
        self.site_url = os.getenv("SHAREPOINT_SITE_URL", "")

        self._site_id_cache: Optional[str] = None
        self._list_id_cache: Dict[str, str] = {}
        self._list_columns_cache: Dict[str, Dict[str, str]] = {}

        if not self.authenticated:
            logger.warning("SharePoint authentication failed.")

    # ── Internal helpers ────────────────────────────────────────────────────────

    def is_authenticated(self) -> bool:
        return self.authenticated

    def get_last_error(self) -> Optional[str]:
        return self.last_error

    def _set_error(self, error: str, log_level: str = "error") -> None:
        self.last_error = error
        getattr(logger, log_level)(error)

    def _get_site_id(self) -> Optional[str]:
        if self._site_id_cache:
            return self._site_id_cache
        if not self.authenticated:
            self._set_error("Not authenticated")
            return None
        if not self.site_url:
            self._set_error("SHAREPOINT_SITE_URL not configured", "warning")
            return None
        try:
            headers = self.auth_manager.get_headers()
            parts = self.site_url.replace("https://", "").split("/")
            hostname = parts[0]
            site_path = "/" + "/".join(parts[1:]) if len(parts) > 1 else ""
            url = f"{self.graph_base_url}/sites/{hostname}:{site_path}"
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                sid = resp.json().get("id")
                if sid:
                    self._site_id_cache = sid
                    return sid
            self._set_error(f"Failed to get site ID: {resp.status_code}")
        except Exception as exc:
            self._set_error(f"Exception resolving site ID: {exc}")
        return None

    def _get_list_id(self, list_name: str) -> Optional[str]:
        if list_name in self._list_id_cache:
            return self._list_id_cache[list_name]
        if not self.authenticated:
            self._set_error("Not authenticated")
            return None
        site_id = self._get_site_id()
        if not site_id:
            return None
        try:
            headers = self.auth_manager.get_headers()
            resp = requests.get(
                f"{self.graph_base_url}/sites/{site_id}/lists",
                headers=headers, timeout=30,
            )
            if resp.status_code == 200:
                for item in resp.json().get("value", []):
                    if item.get("displayName") == list_name or item.get("name") == list_name:
                        lid = item["id"]
                        self._list_id_cache[list_name] = lid
                        return lid
                self._set_error(f"List '{list_name}' not found", "warning")
            else:
                self._set_error(f"Failed to get lists: {resp.status_code}")
        except Exception as exc:
            self._set_error(f"Exception getting list ID: {exc}")
        return None

    def _parse_list_items(self, items: List[Dict]) -> pd.DataFrame:
        if not items:
            return pd.DataFrame()
        rows = []
        for item in items:
            fields = item.get("fields", {})
            row = {
                "id": item.get("id"),
                "created": item.get("createdDateTime"),
                "modified": item.get("lastModifiedDateTime"),
            }
            row.update(fields)
            rows.append(row)
        return pd.DataFrame(rows)

    def _fetch_with_pagination(self, url: str) -> List[Dict]:
        all_items: List[Dict] = []
        headers = self.auth_manager.get_headers()
        while url:
            resp = requests.get(url, headers=headers, timeout=60)
            if resp.status_code != 200:
                self._set_error(f"Failed to get list items: {resp.status_code}")
                return all_items
            data = resp.json()
            all_items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return all_items

    # ── Public read methods ─────────────────────────────────────────────────────

    def get_list_data(
        self,
        list_id: str,
        start_date=None,
        end_date=None,
    ) -> Optional[pd.DataFrame]:
        if not self.authenticated:
            self._set_error("Not authenticated")
            return None
        site_id = self._get_site_id()
        if not site_id:
            return None
        try:
            url = f"{self.graph_base_url}/sites/{site_id}/lists/{list_id}/items?$expand=fields&$top=999"
            all_items = self._fetch_with_pagination(url)
            df = self._parse_list_items(all_items)
            if df.empty:
                return df
            if (start_date or end_date) and "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
                if start_date:
                    df = df[df["Date"] >= pd.to_datetime(start_date)]
                if end_date:
                    df = df[df["Date"] <= pd.to_datetime(end_date)]
                df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
            logger.info(f"Retrieved {len(df)} items from list {list_id}")
            return df
        except Exception as exc:
            self._set_error(f"Exception fetching list data: {exc}")
            return None

    def get_solar_data(self, start_date=None, end_date=None) -> Optional[pd.DataFrame]:
        """Read from Unified_Solar_List."""
        list_name = UNIFIED_SOLAR_LIST_NAME
        list_id = self._get_list_id(list_name)
        if not list_id:
            self._set_error(f"Could not find list: {list_name}", "warning")
            return None
        return self.get_list_data(list_id, self._parse_date(start_date), self._parse_date(end_date))

    def get_grid_data(self, start_date=None, end_date=None) -> Optional[pd.DataFrame]:
        """Read from Grid_Diesel_List (grid columns only)."""
        list_name = GRID_DIESEL_LIST_NAME
        list_id = self._get_list_id(list_name)
        if not list_id:
            self._set_error(f"Could not find list: {list_name}", "warning")
            return None
        df = self.get_list_data(list_id, self._parse_date(start_date), self._parse_date(end_date))
        # Return only grid-relevant columns
        if df is not None and not df.empty:
            grid_cols = [
                c for c in df.columns
                if any(k in c.lower() for k in ("date", "time", "grid", "ambient", "operator"))
            ]
            return df[grid_cols] if grid_cols else df
        return df

    def get_diesel_data(self, start_date=None, end_date=None) -> Optional[pd.DataFrame]:
        """Read from Grid_Diesel_List (diesel columns only)."""
        list_name = GRID_DIESEL_LIST_NAME
        list_id = self._get_list_id(list_name)
        if not list_id:
            self._set_error(f"Could not find list: {list_name}", "warning")
            return None
        df = self.get_list_data(list_id, self._parse_date(start_date), self._parse_date(end_date))
        if df is not None and not df.empty:
            diesel_cols = [
                c for c in df.columns
                if any(k in c.lower() for k in ("date", "time", "diesel", "ambient", "operator"))
            ]
            return df[diesel_cols] if diesel_cols else df
        return df

    def get_ops_manual_data(self, start_date=None, end_date=None) -> Optional[pd.DataFrame]:
        """Alias for Grid_Diesel_List (backwards compat with scheduler_service)."""
        return self.get_grid_data(start_date, end_date)

    def get_solar_master_unified(self, start_date=None, end_date=None) -> Optional[pd.DataFrame]:
        """Read from Master_Data_List."""
        list_name = MASTER_DATA_LIST_NAME
        list_id = self._get_list_id(list_name)
        if not list_id:
            self._set_error(f"Could not find list: {list_name}", "warning")
            return None
        return self.get_list_data(list_id, self._parse_date(start_date), self._parse_date(end_date))

    def get_master_data(self, start_date=None, end_date=None) -> Optional[pd.DataFrame]:
        """Read from Master_Data_List (primary public alias)."""
        return self.get_solar_master_unified(start_date, end_date)

    def get_list_item_by_date(self, list_name: str, target_date: date) -> Optional[Dict]:
        """Find a single item matching Date == target_date (used by scheduler_service)."""
        list_id = self._get_list_id(list_name)
        if not list_id:
            return None
        site_id = self._get_site_id()
        if not site_id:
            return None
        try:
            headers = self.auth_manager.get_headers()
            date_str = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
            filter_expr = f"fields/Date eq '{date_str}'"
            url = (
                f"{self.graph_base_url}/sites/{site_id}/lists/{list_id}/items"
                f"?$expand=fields&$filter={requests.utils.quote(filter_expr)}&$top=1"
            )
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                items = resp.json().get("value", [])
                if items:
                    item = items[0]
                    result = {"id": item["id"]}
                    result.update(item.get("fields", {}))
                    return result
            else:
                # Fallback: fetch all and filter
                df = self.get_list_data(list_id, target_date, target_date)
                if df is not None and not df.empty:
                    return df.iloc[0].to_dict()
        except Exception as exc:
            self._set_error(f"Exception in get_list_item_by_date: {exc}")
        return None

    # ── Public write methods ────────────────────────────────────────────────────

    def create_list_item(self, list_name: str, field_values: Dict[str, Any]) -> Optional[Dict]:
        list_id = self._get_list_id(list_name)
        if not list_id:
            return None
        site_id = self._get_site_id()
        if not site_id:
            return None
        try:
            headers = self.auth_manager.get_headers()
            url = f"{self.graph_base_url}/sites/{site_id}/lists/{list_id}/items"
            resp = requests.post(url, headers=headers, json={"fields": field_values}, timeout=30)
            if resp.status_code not in (200, 201):
                self._set_error(f"Failed to create item: {resp.status_code} — {resp.text}")
                return None
            return resp.json()
        except Exception as exc:
            self._set_error(f"Exception creating list item: {exc}")
            return None

    def update_list_item(
        self, list_name: str, item_id: str, field_values: Dict[str, Any]
    ) -> Optional[Dict]:
        list_id = self._get_list_id(list_name)
        if not list_id:
            return None
        site_id = self._get_site_id()
        if not site_id:
            return None
        try:
            headers = self.auth_manager.get_headers()
            url = f"{self.graph_base_url}/sites/{site_id}/lists/{list_id}/items/{item_id}/fields"
            resp = requests.patch(url, headers=headers, json=field_values, timeout=30)
            if resp.status_code != 200:
                self._set_error(f"Failed to update item: {resp.status_code} — {resp.text}")
                return None
            return resp.json()
        except Exception as exc:
            self._set_error(f"Exception updating list item: {exc}")
            return None

    def upsert_list_item(self, list_name: str, field_values: Dict[str, Any], key_field: str = "Title") -> Optional[Dict]:
        """Create or update by a unique key field (default: Title)."""
        list_id = self._get_list_id(list_name)
        if not list_id:
            return None
        site_id = self._get_site_id()
        if not site_id:
            return None
        key_value = field_values.get(key_field, "")
        try:
            headers = self.auth_manager.get_headers()
            filter_expr = f"fields/{key_field} eq '{key_value}'"
            url = (
                f"{self.graph_base_url}/sites/{site_id}/lists/{list_id}/items"
                f"?$expand=fields&$filter={requests.utils.quote(filter_expr)}&$top=1"
            )
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            items = resp.json().get("value", [])
            if items:
                item_id = items[0]["id"]
                return self.update_list_item(list_name, item_id, field_values)
            else:
                return self.create_list_item(list_name, field_values)
        except Exception as exc:
            self._set_error(f"Exception in upsert_list_item: {exc}")
            return None

    # ── Utility ────────────────────────────────────────────────────────────────

    def _parse_date(self, date_input) -> Optional[date]:
        if date_input is None:
            return None
        if isinstance(date_input, date):
            return date_input
        if isinstance(date_input, datetime):
            return date_input.date()
        if isinstance(date_input, str):
            try:
                return pd.to_datetime(date_input).date()
            except Exception:
                return None
        return None

    def _normalize_field_name(self, field_name: Any) -> str:
        normalized = str(field_name or "").replace("_x0020_", " ").replace("_", " ").strip().lower()
        return re.sub(r"\s+", "", normalized)

    def health_check(self) -> bool:
        if not self.authenticated:
            return False
        return bool(self._get_site_id())