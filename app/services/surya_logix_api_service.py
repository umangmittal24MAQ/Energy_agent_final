"""
SuryaLogix API Service
Fetches real-time and historical solar generation data from SuryaLogix cloud API
Implements authentication, data parsing, and DataFrame conversion for compatibility
with existing data loaders.
"""
import logging
import json
import os
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, IntegrationError, DataValidationError
from app.core.retry import retry_with_backoff

logger = logging.getLogger(__name__)

# Timezone for data processing
IST = ZoneInfo("Asia/Kolkata")

# SuryaLogix API Configuration
SURYALOGIX_BASE_URL = "https://cloud.suryalog.com"
SURYALOGIX_LOGIN_URL = "https://cloud.suryalog.com"
SURYALOGIX_ENDPOINTS = {
    "login": "/",  # Web login page
    "change_plant": "/common/change_plant",
    "live_data": "/livebar/gen_info",
    "day_data": "/livebar/day_data",
    "month_data": "/livebar/month_data",
    "year_data": "/livebar/year_data",
}

# Session management for API calls
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.5


class SuryaLogixAPIService:
    """
    Service for fetching data from SuryaLogix solar monitoring API.

    Handles:
    - Web-based authentication and session management
    - API calls to fetch plant info, live data, and historical data
    - Data parsing and validation
    - DataFrame conversion for compatibility with existing loaders
    - Automatic retry with exponential backoff
    - Error handling and logging
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        plant_id: Optional[str] = None,
        device_id: Optional[str] = None,
        cache_dir: Optional[Path] = None,
    ):
        """
        Initialize SuryaLogix API Service.

        Args:
            username: SuryaLogix login username (can be from environment SURYALOGIX_USERNAME)
            password: SuryaLogix login password (can be from environment SURYALOGIX_PASSWORD)
            plant_id: Plant ID (can be from environment SURYALOGIX_PLANT_ID)
            device_id: Device ID (can be from environment SURYALOGIX_DEVICE_ID)
            cache_dir: Directory for caching API responses (uses config default if not provided)
        """
        self.settings = get_settings()
        self.username = username or os.getenv("SURYALOGIX_USERNAME", "MAQ_Software")
        self.password = password or os.getenv("SURYALOGIX_PASSWORD", "MAQ@1234")
        self.plant_id = plant_id or os.getenv("SURYALOGIX_PLANT_ID")
        self.device_id = device_id or os.getenv("SURYALOGIX_DEVICE_ID")
        self.cache_dir = cache_dir or self.settings.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Initialize session with retry strategy
        self.session = self._create_session()

        # Authentication state
        self.authenticated = False
        self.auth_token = None
        self.session_cookies = None

        # Cache for plant info (avoid repeated calls)
        self._plant_info_cache = None
        self._plant_info_cache_time = None
        self._plant_info_ttl = timedelta(hours=1)

        logger.info(
            f"SuryaLogix API Service initialized for username={self.username}, "
            f"plant_id={self.plant_id}"
        )

    def _create_session(self) -> requests.Session:
        """
        Create a requests session with retry strategy and connection pooling.

        Returns:
            Configured requests.Session object
        """
        session = requests.Session()

        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Add default headers
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": SURYALOGIX_BASE_URL,
        })

        return session

    def _validate_credentials(self) -> None:
        """
        Validate that required credentials are available.

        Raises:
            AuthenticationError: If required credentials are missing
        """
        if not self.username or not self.password:
            raise AuthenticationError(
                "Username and password are required for SuryaLogix authentication. "
                "Provide them during initialization or set SURYALOGIX_USERNAME and SURYALOGIX_PASSWORD environment variables."
            )
        logger.debug("SuryaLogix credentials validated")

    def _authenticate(self) -> None:
        """
        Authenticate with SuryaLogix using web login simulation.

        Raises:
            AuthenticationError: If authentication fails
        """
        if self.authenticated:
            return

        self._validate_credentials()

        try:
            logger.info("Authenticating with SuryaLogix...")

            # Step 1: Get login page to establish session
            response = self.session.get(SURYALOGIX_LOGIN_URL, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            # Step 2: Submit login credentials
            login_data = {
                "loginId": self.username,
                "password": self.password,
            }

            login_url = f"{SURYALOGIX_BASE_URL}/common/login"
            response = self.session.post(
                login_url,
                data=login_data,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )

            if response.status_code not in [200, 302]:
                raise AuthenticationError(
                    f"SuryaLogix login failed with status {response.status_code}: {response.text}"
                )

            # Check if login was successful by looking for session cookies or redirect
            if "dashboard" in response.url.lower() or "plant" in response.url.lower():
                self.authenticated = True
                self.session_cookies = self.session.cookies
                logger.info("Successfully authenticated with SuryaLogix")
            else:
                # Try to extract token from response or cookies
                self._extract_auth_token(response)
                if self.auth_token:
                    self.authenticated = True
                    logger.info("Successfully authenticated with SuryaLogix (token extracted)")
                else:
                    raise AuthenticationError("Login failed - no valid session or token found")

        except requests.exceptions.RequestException as e:
            logger.error(f"Authentication request failed: {e}")
            raise AuthenticationError(f"SuryaLogix authentication failed: {e}")

    def _extract_auth_token(self, response: requests.Response) -> None:
        """
        Extract authentication token from login response.

        Args:
            response: Login response object
        """
        # Try to extract token from cookies
        for cookie in self.session.cookies:
            if "token" in cookie.name.lower():
                self.auth_token = cookie.value
                logger.debug("Token extracted from cookies")
                return

        # Try to extract from response JSON
        try:
            data = response.json()
            if "token" in data:
                self.auth_token = data["token"]
                logger.debug("Token extracted from response JSON")
                return
        except:
            pass

        # Try to extract from response text (look for token in HTML/JS)
        response_text = response.text.lower()
        if "token" in response_text:
            # This is a simple extraction - in production, use proper parsing
            logger.debug("Token found in response text")

    @retry_with_backoff(
        max_retries=3,
        initial_delay=1.0,
        max_delay=10.0,
        exceptions=(requests.RequestException, TimeoutError),
    )
    def _make_request(
        self,
        endpoint: str,
        method: str = "POST",
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make HTTP request to SuryaLogix API with retry logic.

        Args:
            endpoint: API endpoint (e.g., '/common/change_plant')
            method: HTTP method (GET or POST)
            data: Request payload for POST requests
            params: Query parameters for GET requests

        Returns:
            Parsed JSON response

        Raises:
            IntegrationError: If request fails after retries
            AuthenticationError: If authentication fails (401/403)
        """
        # Ensure we're authenticated
        self._authenticate()

        url = f"{SURYALOGIX_BASE_URL}{endpoint}"

        # Add token to request data if available
        if data is None:
            data = {}
        if self.auth_token and "token" not in data:
            data["token"] = self.auth_token

        try:
            if method.upper() == "POST":
                response = self.session.post(
                    url,
                    data=data,
                    timeout=REQUEST_TIMEOUT,
                )
            else:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )

            response.raise_for_status()

            result = response.json()
            logger.debug(f"API request successful: {endpoint}")
            return result

        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                logger.error(f"Authentication failed for {endpoint}: {e}")
                # Reset authentication state
                self.authenticated = False
                self.auth_token = None
                raise AuthenticationError(f"SuryaLogix authentication failed: {e}")
            logger.error(f"HTTP error for {endpoint}: {e}")
            raise IntegrationError(f"SuryaLogix API error: {e}")

        except requests.exceptions.Timeout as e:
            logger.error(f"Request timeout for {endpoint}: {e}")
            raise IntegrationError(f"SuryaLogix API timeout: {e}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {endpoint}: {e}")
            raise IntegrationError(f"SuryaLogix API request failed: {e}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse response from {endpoint}: {e}")
            raise IntegrationError(f"Invalid JSON from SuryaLogix API: {e}")

    def _get_plant_info(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Get plant information with caching.

        Args:
            force_refresh: Force refresh from API, ignoring cache

        Returns:
            Plant information dictionary

        Raises:
            IntegrationError: If API call fails
        """
        # Check cache if not forcing refresh
        if (
            not force_refresh
            and self._plant_info_cache is not None
            and self._plant_info_cache_time is not None
        ):
            if datetime.now(tz=IST) - self._plant_info_cache_time < self._plant_info_ttl:
                logger.debug("Using cached plant info")
                return self._plant_info_cache

        self._validate_credentials()

        payload = {
            "plant_id": self.plant_id,
            "device_id": self.device_id,
        }

        response = self._make_request(
            SURYALOGIX_ENDPOINTS["change_plant"],
            method="POST",
            data=payload,
        )

        # Extract plant info from response
        plant_info = response.get("data", {}).get("plantInfo", {})

        if not plant_info:
            raise IntegrationError(
                "No plant information found in SuryaLogix response"
            )

        # Cache the result
        self._plant_info_cache = plant_info
        self._plant_info_cache_time = datetime.now(tz=IST)

        logger.info(f"Retrieved plant info: {plant_info.get('plant_name', 'Unknown')}")
        return plant_info

    @retry_with_backoff(
        max_retries=3,
        initial_delay=1.0,
        max_delay=10.0,
        exceptions=(requests.RequestException, TimeoutError),
    )
    def get_live_data(self) -> pd.DataFrame:
        """
        Fetch current live solar generation data.

        Returns:
            DataFrame with columns: Timestamp, AC Power (kW), DC Power (kW),
                                   Day Generation (kWh), CUF (%), Status

        Raises:
            IntegrationError: If API call fails
            DataValidationError: If data parsing fails
        """
        self._validate_credentials()

        logger.info("Fetching live data from SuryaLogix API")

        payload = {
            "plant_id": self.plant_id,
            "device_id": self.device_id,
        }

        try:
            response = self._make_request(
                SURYALOGIX_ENDPOINTS["live_data"],
                method="POST",
                data=payload,
            )

            # Parse live data
            live_data = response.get("data", {})
            livebar = live_data.get("livebar", {})
            last_log = live_data.get("lastLogData", {}).get("plant", {})

            timestamp = datetime.fromtimestamp(
                livebar.get("sDate", 0) / 1000.0, tz=IST
            )

            # Extract key metrics
            ac_power_kw = livebar.get("sKW", 0) / 1000.0  # Convert from W to kW
            day_generation_kwh = livebar.get("sDKWH", 0) / 1000.0  # Convert from Wh to kWh
            daily_cuf = livebar.get("sCUF", 0)

            # Create DataFrame
            df = pd.DataFrame([
                {
                    "Timestamp": timestamp,
                    "Date": timestamp.strftime("%Y-%m-%d"),
                    "Time": timestamp.strftime("%H:%M:%S"),
                    "AC Power (kW)": float(ac_power_kw),
                    "DC Power (kW)": last_log.get("DCW", 0) / 1000.0,
                    "Day Generation (kWh)": float(day_generation_kwh),
                    "CUF (%)": float(daily_cuf),
                    "Device Status": last_log.get("device_status_code", 0),
                }
            ])

            # Standardize column names for compatibility
            df = df.rename(columns={
                "AC Power (kW)": "AC Power (kW)",
                "DC Power (kW)": "DC Power (kW)",
            })

            logger.info(f"Live data fetched successfully: {len(df)} records")
            return df

        except KeyError as e:
            logger.error(f"Failed to parse live data response: {e}")
            raise DataValidationError(f"Invalid live data format from SuryaLogix: {e}")
        except Exception as e:
            logger.error(f"Error fetching live data: {e}")
            raise

    def get_daily_generation(self, date: Optional[str] = None) -> pd.DataFrame:
        """
        Fetch daily generation data for a specific date.

        Args:
            date: Date in YYYY-MM-DD format (default: today)

        Returns:
            DataFrame with daily generation summary

        Raises:
            IntegrationError: If API call fails
            DataValidationError: If data parsing fails
        """
        if date is None:
            date = datetime.now(tz=IST).strftime("%Y-%m-%d")

        logger.info(f"Fetching daily generation data for {date}")

        self._validate_credentials()

        # Parse date
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError:
            raise DataValidationError(f"Invalid date format: {date}. Use YYYY-MM-DD")

        payload = {
            "plant_id": self.plant_id,
            "device_id": self.device_id,
            "date": int(date_obj.timestamp()),
        }

        try:
            response = self._make_request(
                SURYALOGIX_ENDPOINTS["day_data"],
                method="POST",
                data=payload,
            )

            # Parse response
            day_data = response.get("data", {}).get("day_data", [])

            if not day_data:
                logger.warning(f"No day data found for {date}")
                return pd.DataFrame()

            # Convert to DataFrame
            records = []
            for entry in day_data:
                records.append({
                    "Date": date,
                    "Time": entry.get("time", "00:00:00"),
                    "AC Power (kW)": float(entry.get("acPower", 0)) / 1000.0,
                    "DC Power (kW)": float(entry.get("dcPower", 0)) / 1000.0,
                    "Energy (kWh)": float(entry.get("energy", 0)) / 1000.0,
                })

            df = pd.DataFrame(records)
            logger.info(f"Daily generation data fetched: {len(df)} records for {date}")
            return df

        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse daily generation data: {e}")
            raise DataValidationError(f"Invalid daily data format from SuryaLogix: {e}")

    def get_historical_data(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        data_type: str = "day",
    ) -> pd.DataFrame:
        """
        Fetch historical generation data for a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format (default: start_date)
            data_type: Type of data to fetch - 'day', 'month', or 'year'

        Returns:
            DataFrame with historical generation data

        Raises:
            IntegrationError: If API call fails
            DataValidationError: If data parsing fails
        """
        if end_date is None:
            end_date = start_date

        logger.info(
            f"Fetching {data_type} data from {start_date} to {end_date}"
        )

        self._validate_credentials()

        # Parse dates
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=IST)
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError as e:
            raise DataValidationError(f"Invalid date format: {e}")

        # Determine endpoint based on data_type
        if data_type == "month":
            endpoint = SURYALOGIX_ENDPOINTS["month_data"]
        elif data_type == "year":
            endpoint = SURYALOGIX_ENDPOINTS["year_data"]
        else:
            endpoint = SURYALOGIX_ENDPOINTS["day_data"]

        all_records = []

        # Iterate through date range
        current_date = start_dt
        while current_date <= end_dt:
            payload = {
                "plant_id": self.plant_id,
                "device_id": self.device_id,
                "date": int(current_date.timestamp()),
            }

            try:
                response = self._make_request(
                    endpoint,
                    method="POST",
                    data=payload,
                )

                # Parse response
                data_key = f"{data_type}_data"
                records = response.get("data", {}).get(data_key, [])

                for entry in records:
                    all_records.append({
                        "Date": current_date.strftime("%Y-%m-%d"),
                        "Time": entry.get("time", "00:00:00"),
                        "Energy (kWh)": float(entry.get("energy", 0)) / 1000.0,
                        "AC Power (kW)": float(entry.get("acPower", 0)) / 1000.0,
                        "DC Power (kW)": float(entry.get("dcPower", 0)) / 1000.0,
                    })

            except Exception as e:
                logger.warning(
                    f"Failed to fetch {data_type} data for {current_date}: {e}"
                )

            # Increment date based on data_type
            if data_type == "month":
                current_date = (current_date + timedelta(days=32)).replace(day=1)
            elif data_type == "year":
                current_date = current_date.replace(year=current_date.year + 1)
            else:
                current_date = current_date + timedelta(days=1)

        if not all_records:
            logger.warning(f"No historical data found for {start_date} to {end_date}")
            return pd.DataFrame()

        df = pd.DataFrame(all_records)
        logger.info(
            f"Historical data fetched: {len(df)} records from {start_date} to {end_date}"
        )
        return df

    def get_unified_data(self) -> pd.DataFrame:
        """
        Fetch and combine live and daily data into unified DataFrame.

        Returns:
            Unified DataFrame compatible with existing data loaders
        """
        logger.info("Fetching unified data from SuryaLogix")

        try:
            live_df = self.get_live_data()

            if live_df.empty:
                logger.warning("Live data is empty")
                return pd.DataFrame()

            # Add standard columns for compatibility
            live_df["Source"] = "SuryaLogix"
            live_df["Plant Type"] = "Solar"

            logger.info(f"Unified data prepared: {len(live_df)} records")
            return live_df

        except Exception as e:
            logger.error(f"Error fetching unified data: {e}")
            raise

    def validate_connection(self) -> bool:
        """
        Validate connection to SuryaLogix API.

        Returns:
            True if connection is valid, False otherwise
        """
        try:
            logger.info("Validating SuryaLogix API connection")
            plant_info = self._get_plant_info(force_refresh=True)
            logger.info("SuryaLogix API connection validated successfully")
            return True
        except Exception as e:
            logger.error(f"SuryaLogix API connection validation failed: {e}")
            return False

    def close(self) -> None:
        """Close session and cleanup resources."""
        if self.session:
            self.session.close()
            logger.info("SuryaLogix API Service session closed")