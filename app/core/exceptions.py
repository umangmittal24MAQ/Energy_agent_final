"""
Custom exceptions for the Energy Dashboard application
"""


class EnergyDashboardError(Exception):
    """Base exception for all Energy Dashboard errors"""

    pass


class ConfigurationError(EnergyDashboardError):
    """Raised when configuration is invalid or missing"""

    pass


class AuthenticationError(EnergyDashboardError):
    """Raised when authentication fails"""

    pass


class AuthorizationError(EnergyDashboardError):
    """Raised when user lacks required permissions"""

    pass


class DataNotFoundError(EnergyDashboardError):
    """Raised when requested data is not found"""

    pass


class DataValidationError(EnergyDashboardError):
    """Raised when data validation fails"""

    pass


class IntegrationError(EnergyDashboardError):
    """Raised when external integration fails"""

    pass


class SharePointError(IntegrationError):
    """Raised when SharePoint operation fails"""

    pass


class GoogleSheetsError(IntegrationError):
    """Raised when Google Sheets operation fails"""

    pass


class IngestionError(EnergyDashboardError):
    """Raised when data ingestion fails"""

    pass


class ExportError(EnergyDashboardError):
    """Raised when data export fails"""

    pass


class SchedulerError(EnergyDashboardError):
    """Raised when scheduler operation fails"""

    pass


class DatabaseError(EnergyDashboardError):
    """Raised when database operation fails"""

    pass
