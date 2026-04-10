"""
ingestion_bridge.py  (FIXED)
=============================
Removed dead `google_sheets_writer.backfill_inverter_columns` reference that
would crash at import time after migration. SharePoint writer is now the only
write target, via sharepoint_list_data_service (List API, not Excel files).
"""
from __future__ import annotations

import logging
from typing import Any, Tuple

logger = logging.getLogger(__name__)


def get_loader_processor() -> Tuple[Any, Any]:
    """Return loaded loader and processor modules from app.agents.ingestion."""
    try:
        from app.agents import ingestion
        return ingestion.loader, ingestion.processor
    except ImportError as exc:
        logger.warning(f"Could not import ingestion modules: {exc}")
        return None, None


def run_ingestion_once() -> bool:
    """Execute one ingestion run via ingestion_orchestrator.run_once()."""
    try:
        from app.agents.ingestion import ingestion_orchestrator
        if not hasattr(ingestion_orchestrator, "run_once"):
            logger.error("ingestion_orchestrator.py does not define run_once()")
            return False
        logger.info("Triggering one-time ingestion pipeline run")
        return bool(ingestion_orchestrator.run_once())
    except ImportError as exc:
        logger.error(f"Failed to import ingestion orchestrator: {exc}")
        return False


def run_inverter_backfill_once() -> bool:
    """
    DEPRECATED: was used for Google Sheets backfill — no longer needed.
    SharePoint list items are written with all columns from the first insert.
    Returns True (no-op) to avoid breaking any callers.
    """
    logger.info("run_inverter_backfill_once: no-op after SharePoint migration")
    return True


def get_sharepoint_writer():
    """
    Return the SharePointListDataService (List API) for write operations.
    The old Excel-based sharepoint_writer is no longer used.
    """
    try:
        from app.services.sharepoint_list_data_service import SharePointListDataService
        writer = SharePointListDataService()
        if not writer.is_authenticated():
            logger.warning("SharePoint not authenticated — writer unavailable")
            return None
        return writer
    except Exception as exc:
        logger.error(f"Failed to load SharePoint list service: {exc}")
        return None


def write_to_sharepoint_once(list_name: str, data: list) -> bool:
    """
    Upsert rows into a SharePoint List.

    Args:
        list_name: Name of the SharePoint list (e.g. 'Unified_Solar_List')
        data: List of field-value dicts to upsert (key = Title for dedup)
    """
    if not data:
        logger.warning("write_to_sharepoint_once: empty data, skipping")
        return True
    try:
        writer = get_sharepoint_writer()
        if not writer:
            return False
        success_count = 0
        for row in data:
            result = writer.upsert_list_item(list_name, row, key_field="Title")
            if result is not None:
                success_count += 1
        logger.info(f"Wrote {success_count}/{len(data)} rows to SharePoint list: {list_name}")
        return success_count == len(data)
    except Exception as exc:
        logger.error(f"Exception writing to SharePoint: {exc}")
        return False