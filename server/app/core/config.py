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

class ExcelFileConfig(BaseModel):
    """Configuration for a SharePoint Excel data source"""
    name: str
    filename: str
    tab_name: str = "Sheet1"
    date_field: str = "Date"

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

    # SharePoint Graph API
    sharepoint_tenant_id: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_TENANT_ID")
    sharepoint_client_id: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_CLIENT_ID")
    sharepoint_client_secret: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_CLIENT_SECRET")
    sharepoint_site_url: Optional[str] = Field(default=None, validation_alias="SHAREPOINT_SITE_URL")
    
    # Storage & Paths
    app_root_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2], validation_alias="APP_ROOT_DIR")
    cache_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard" / "output", validation_alias="CACHE_DIR")
    scheduler_config_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard" / "scheduler_config.json", validation_alias="SCHEDULER_CONFIG_PATH")
    scheduler_log_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "energy-dashboard" / "output" / "scheduler_log.json", validation_alias="SCHEDULER_LOG_PATH")
    sharepoint_token_cache_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / ".sharepoint_token_cache", validation_alias="SHAREPOINT_TOKEN_CACHE_PATH")

    # Email Base Config (Dynamic overrides happen in scheduler_config.json)
    smtp_server: Optional[str] = Field(default="smtp.gmail.com", validation_alias="SMTP_SERVER")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_username: Optional[str] = Field(default=None, validation_alias="SMTP_USERNAME")
    smtp_password: Optional[str] = Field(default=None, validation_alias="SMTP_PASSWORD")
    smtp_from_email: Optional[str] = Field(default=None, validation_alias="SMTP_FROM_EMAIL")
    smtp_use_tls: bool = Field(default=True, validation_alias="SMTP_USE_TLS")

    # General
    timezone: str = Field(default="Asia/Kolkata", validation_alias="TIMEZONE")
    allowed_origins: str = Field(
        default="http://localhost:5172,http://127.0.0.1:5172,http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="ALLOWED_ORIGINS"
    )

    # Cost Configuration
    grid_cost_per_unit: float = Field(default=7.11, validation_alias="GRID_COST_PER_UNIT")
    diesel_cost_per_unit: float = Field(default=25.0, validation_alias="DIESEL_COST_PER_UNIT")
    solar_cost_per_unit: float = Field(default=0.0, validation_alias="SOLAR_COST_PER_UNIT")
    solar_target_percentage: float = Field(default=25.0, validation_alias="SOLAR_TARGET_PERCENTAGE")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "allow"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    def get_excel_files_config(self) -> Dict[str, ExcelFileConfig]:
        """Provides the exact file names for the 3 core SharePoint Excel files."""
        return {
            "master_data": ExcelFileConfig(
                name="master_data",
                filename="Master-data.xlsx"
            ),
            "unified_solar": ExcelFileConfig(
                name="unified_solar",
                filename="UnifiedSolarData.xlsx"
            ),
            "grid_and_diesel": ExcelFileConfig(
                name="grid_and_diesel",
                filename="Electrical Optimization (1).xlsx"
            )
        }

    def get_cost_config(self) -> CostConfig:
        return CostConfig(
            grid_cost_per_unit=self.grid_cost_per_unit,
            diesel_cost_per_unit=self.diesel_cost_per_unit,
            solar_cost_per_unit=self.solar_cost_per_unit,
            solar_target_percentage=self.solar_target_percentage,
        )

@lru_cache
def get_settings() -> Settings:
    return Settings()