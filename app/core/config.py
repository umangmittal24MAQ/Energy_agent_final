"""
Configuration management for the Energy Dashboard application
Azure-ready with environment variable support for all settings
"""
import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field, BaseModel

logger = logging.getLogger(__name__)


class SheetConfig(BaseModel):
    """Configuration for a single sheet/data source"""
    name: str
    sheet_url: str = ""
    sheet_id: str = ""
    tab_name: str = "Sheet1"
    timestamp_field: str = "Time"
    date_field: str = "Date"
    numeric_columns: List[str] = Field(default_factory=list)


class CostConfig(BaseModel):
    """Cost configuration for energy calculations"""
    grid_cost_per_unit: float = Field(default=7.11, description="Grid cost per kWh in INR")
    diesel_cost_per_unit: float = Field(default=25.0, description="Diesel cost per liter")
    solar_cost_per_unit: float = Field(default=0.0, description="Solar cost per kWh")
    solar_target_percentage: float = Field(default=25.0, description="Solar generation target (%)")


class Settings(BaseSettings):
    """Application settings"""

    # Application
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    app_name: str = Field(default="Energy Dashboard", validation_alias="APP_NAME")
    app_version: str = Field(default="1.0.0", validation_alias="APP_VERSION")
    debug: bool = Field(default=True)
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # API Server
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    api_reload: bool = Field(default=True, validation_alias="API_RELOAD")
    frontend_url: str = Field(default="http://localhost:5173", validation_alias="FRONTEND_URL")

    # Database
    database_url: Optional[str] = Field(default=None, validation_alias="DATABASE_URL")
    database_echo: bool = Field(default=False, validation_alias="SQLALCHEMY_ECHO")

    # SharePoint
    sharepoint_tenant_id: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_TENANT_ID")
    sharepoint_client_id: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_CLIENT_ID")
    sharepoint_client_secret: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_CLIENT_SECRET")
    sharepoint_site_url: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_SITE_URL")
    sharepoint_list_id: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_LIST_ID")

    # Storage & legacy paths
    app_root_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2], validation_alias="APP_ROOT_DIR")
    legacy_root_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard", validation_alias="LEGACY_ROOT_DIR")
    legacy_ingestion_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard" / "Ingestion-agent", validation_alias="LEGACY_INGESTION_DIR")
    cache_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard" / "output", validation_alias="CACHE_DIR")
    scheduler_config_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard" / "scheduler_config.json", validation_alias="SCHEDULER_CONFIG_PATH")
    scheduler_log_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard" / "output" / "scheduler_log.json", validation_alias="SCHEDULER_LOG_PATH")
    uploaded_templates_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard" / "uploaded_templates", validation_alias="UPLOADED_TEMPLATES_DIR")
    sharepoint_token_cache_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / ".sharepoint_token_cache", validation_alias="SHAREPOINT_TOKEN_CACHE_PATH")

    # Email
    smtp_server: Optional[str] = Field(default=None, validation_alias="SMTP_SERVER")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_username: Optional[str] = Field(default=None, validation_alias="SMTP_USERNAME")
    smtp_password: Optional[str] = Field(default=None, validation_alias="SMTP_PASSWORD")
    smtp_from_email: Optional[str] = Field(default=None, validation_alias="SMTP_FROM_EMAIL")
    smtp_use_tls: bool = Field(default=True, validation_alias="SMTP_USE_TLS")

    # Scheduling
    schedule_ingestion_daily: str = Field(default="09:00", validation_alias="SCHEDULE_INGESTION_DAILY")
    schedule_email_daily: str = Field(default="18:00", validation_alias="SCHEDULE_EMAIL_DAILY")
    timezone: str = Field(default="UTC", validation_alias="TIMEZONE")

    # Data Ingestion
    data_ingestion_timeout: int = Field(default=3600, validation_alias="DATA_INGESTION_TIMEOUT")
    data_cache_expiry: int = Field(default=3600, validation_alias="DATA_CACHE_EXPIRY")
    data_retention_days: int = Field(default=90, validation_alias="DATA_RETENTION_DAYS")

    # API Keys
    groq_api_key: Optional[str] = Field(default=None, validation_alias="GROQ_API_KEY")

    # CORS
    allowed_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="ALLOWED_ORIGINS",
    )

    # Monitoring
    sentry_dsn: Optional[str] = Field(default=None, validation_alias="SENTRY_DSN")
    log_file_path: str = Field(default="./backend/logs/app.log", validation_alias="LOG_FILE_PATH")

    # Cost Configuration (from legacy config)
    grid_cost_per_unit: float = Field(default=7.11, validation_alias="GRID_COST_PER_UNIT")
    diesel_cost_per_unit: float = Field(default=25.0, validation_alias="DIESEL_COST_PER_UNIT")
    solar_cost_per_unit: float = Field(default=0.0, validation_alias="SOLAR_COST_PER_UNIT")
    solar_target_percentage: float = Field(default=25.0, validation_alias="SOLAR_TARGET_PERCENTAGE")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "allow"  # Allow extra fields

    @property
    def is_production(self) -> bool:
        """Check if running in production"""
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development"""
        return self.app_env.lower() == "development"

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse allowed origins as list"""
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    def get_sheets_config(self) -> Dict[str, SheetConfig]:
        """
        Get Google Sheets configuration from env var or return defaults.
        Azure-ready: supports both environment variable JSON and defaults.
        """
        if self.sheets_config_json:
            try:
                config_dict = json.loads(self.sheets_config_json)
                return {
                    key: SheetConfig(**config)
                    for key, config in config_dict.items()
                }
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse SHEETS_CONFIG_JSON: {e}. Using defaults.")

        # Default sheet configurations
        return self._get_default_sheets_config()

    def _get_default_sheets_config(self) -> Dict[str, SheetConfig]:
        """Default sheet configuration - now using mock data, Google Sheets URLs removed"""
        return {
            "unified_solar": SheetConfig(
                name="UnifiedSolarData",
                sheet_url="",
                sheet_id="",
                tab_name="Sheet1",
                timestamp_field="Time",
                date_field="Date",
                numeric_columns=[
                    'DC Power (kW)', 'AC Power (kW)', 'Current Total (A)',
                    'Current Average (A)', 'Active Power (kW)', 'Apparent Power (kVA)',
                    'Power Factor', 'Frequency (Hz)',
                    'Voltage Phase-to-Phase (V)', 'Voltage Phase-to-Neutral (V)',
                    'V1 (V)', 'V2 (V)', 'V3 (V)',
                    'Day Generation (kWh)', 'Day Import (kWh)', 'Day Export (kWh)',
                    'Total Import (kWh)', 'Total Export (kWh)',
                    'DC Capacity (kWp)', 'AC Capacity (kW)'
                ]
            ),
            "last_7_days": SheetConfig(
                name="last_7_days",
                sheet_url="",
                sheet_id="",
                tab_name="Sheet1",
                timestamp_field="Date",
                date_field="Date",
                numeric_columns=['Start Value', 'End Value', 'Generation (Wh)']
            ),
            "smb_status": SheetConfig(
                name="SMB_Status",
                sheet_url="",
                sheet_id="",
                tab_name="Sheet1",
                timestamp_field="Date",
                date_field="Date",
                numeric_columns=['SMB1', 'SMB2', 'SMB3', 'SMB4', 'SMB5']
            ),
            "grid_and_diesel": SheetConfig(
                name="grid_and_diesel",
                sheet_url="",
                sheet_id="",
                tab_name="Sheet1",
                timestamp_field="Time",
                date_field="Date",
                numeric_columns=[
                    'Grid Units Consumed (KWh)', 'DG Units Consumed (KWh)',
                    'Total Units Consumed in INR', 'Grid Cost (INR)',
                    'Diesel Cost (INR)', 'Total Cost (INR)', 'Energy Saving (INR)'
                ]
            ),
            "master_data": SheetConfig(
                name="master_data",
                sheet_url="",
                sheet_id="",
                tab_name="Sheet1",
                timestamp_field="Time",
                date_field="Date",
                numeric_columns=[
                    'Grid Units Consumed (KWh)', 
                    'Solar Units Consumed(KWh)', 'Total Units Consumed (KWh)',
                    'Total Units Consumed in INR', 'Energy Saving in INR',
                    'Number of Panels Cleaned', 'Diesel consumed',
                    'Water treated through STP', 'Water treated through WTP'
                ]
            ),
        }

    def get_cost_config(self) -> CostConfig:
        """Get cost configuration from Settings"""
        return CostConfig(
            grid_cost_per_unit=self.grid_cost_per_unit,
            diesel_cost_per_unit=self.diesel_cost_per_unit,
            solar_cost_per_unit=self.solar_cost_per_unit,
            solar_target_percentage=self.solar_target_percentage,
        )


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance

    Returns:
        Settings instance
    """
    return Settings()
