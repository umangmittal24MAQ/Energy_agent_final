"""
Cache Service
Provides in-memory caching with TTL and SQLite fallback for data persistence
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from pathlib import Path
import pandas as pd

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Cache DB path
CACHE_DB_PATH = get_settings().cache_dir / ".cache"
CACHE_DB_PATH.mkdir(parents=True, exist_ok=True)


def _convert_timestamps_to_strings(obj: Any) -> Any:
    """
    Recursively convert Pandas Timestamp objects to ISO strings for JSON serialization
    """
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _convert_timestamps_to_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_timestamps_to_strings(item) for item in obj]
    else:
        return obj


class CacheService:
    """In-memory cache with SQLite persistence and TTL support"""
    
    def __init__(self, default_ttl_seconds: int = 600):
        """
        Initialize cache service
        
        Args:
            default_ttl_seconds: Default time-to-live for cache entries (default: 10 minutes)
        """
        self.default_ttl = timedelta(seconds=default_ttl_seconds)
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.RLock()
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database for persistence"""
        try:
            db_file = CACHE_DB_PATH / "cache.db"
            db_file.parent.mkdir(parents=True, exist_ok=True)
            
            conn = sqlite3.connect(str(db_file), check_same_thread=False)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cache_entries (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ttl_seconds INTEGER DEFAULT 600
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"Cache database initialized at {db_file}")
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.error(f"SQLite database error initializing cache: {e}")
        except OSError as e:
            logger.error(f"File system error initializing cache database: {e}")
        except Exception as e:
            logger.error(f"Unexpected error initializing cache database: {type(e).__name__}: {e}", exc_info=True)
    
    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None):
        """
        Store a value in cache
        
        Args:
            key: Cache key
            value: Value to cache (dict, DataFrame, or JSON-serializable object)
            ttl_seconds: Time-to-live in seconds (None = use default)
        """
        with self.lock:
            try:
                ttl = ttl_seconds or self.default_ttl.total_seconds()
                
                # Convert DataFrame to dict for storage
                if isinstance(value, pd.DataFrame):
                    cached_value = value.to_dict('records')
                else:
                    cached_value = value
                
                # Store in memory
                self.cache[key] = {
                    'value': cached_value,
                    'timestamp': datetime.now(),
                    'ttl': timedelta(seconds=ttl)
                }
                
                # Persist to database
                self._persist_to_db(key, cached_value, int(ttl))
                
                logger.debug(f"Cached {key} with TTL {ttl}s")
            except (TypeError, ValueError) as e:
                logger.warning(f"Data type error caching {key}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error caching {key}: {type(e).__name__}: {e}", exc_info=True)
    
    def get(self, key: str, for_frontend: bool = False) -> Optional[Any]:
        """
        Retrieve a value from cache
        
        Args:
            key: Cache key
            for_frontend: If True, return as JSON-compatible dict; if False, return raw value
            
        Returns:
            Cached value if found and not expired, None otherwise
        """
        with self.lock:
            # Check memory cache first
            if key in self.cache:
                entry = self.cache[key]
                if self._is_valid(entry):
                    value = entry['value']
                    if for_frontend:
                        return value  # Already JSON-compatible
                    return value
                else:
                    # Entry expired, remove it
                    del self.cache[key]
                    self._remove_from_db(key)
            
            # Try to load from database (cache miss or expired)
            return self._load_from_db(key)
    
    def is_stale(self, key: str) -> bool:
        """Check if cache entry exists and is still fresh"""
        with self.lock:
            if key not in self.cache:
                return True
            
            entry = self.cache[key]
            return not self._is_valid(entry)
    
    def _is_valid(self, entry: Dict[str, Any]) -> bool:
        """Check if cache entry is still valid"""
        timestamp = entry.get('timestamp')
        ttl = entry.get('ttl')
        
        if not timestamp or not ttl:
            return False
        
        return datetime.now() - timestamp < ttl
    
    def _persist_to_db(self, key: str, value: Any, ttl_seconds: int):
        """Persist value to SQLite database"""
        try:
            db_file = CACHE_DB_PATH / "cache.db"
            conn = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
            cursor = conn.cursor()
            
            # Convert Pandas Timestamps to strings for JSON serialization
            json_safe_value = _convert_timestamps_to_strings(value)
            json_value = json.dumps(json_safe_value)
            
            cursor.execute('''
                INSERT OR REPLACE INTO cache_entries (key, value, ttl_seconds, timestamp)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (key, json_value, ttl_seconds))
            
            conn.commit()
            conn.close()
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Data serialization error persisting {key}: {e}")
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.warning(f"SQLite error persisting {key}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error persisting {key}: {type(e).__name__}: {e}")
    
    def _load_from_db(self, key: str) -> Optional[Any]:
        """Load value from SQLite database"""
        try:
            db_file = CACHE_DB_PATH / "cache.db"
            if not db_file.exists():
                return None
            
            conn = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT value, timestamp, ttl_seconds FROM cache_entries
                WHERE key = ?
            ''', (key,))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            json_value, timestamp_str, ttl_seconds = row
            
            # Check if entry is still valid
            timestamp = datetime.fromisoformat(timestamp_str)
            if datetime.now() - timestamp > timedelta(seconds=ttl_seconds):
                self._remove_from_db(key)
                return None
            
            # Parse and return value
            value = json.loads(json_value)
            
            # Restore to memory cache
            self.cache[key] = {
                'value': value,
                'timestamp': timestamp,
                'ttl': timedelta(seconds=ttl_seconds)
            }
            
            logger.debug(f"Restored {key} from database")
            return value
        except FileNotFoundError:
            logger.debug(f"Cache database file not found")
            return None
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Data deserialization error loading {key}: {e}")
            return None
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.warning(f"SQLite error loading {key}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error loading {key} from database: {type(e).__name__}: {e}")
            return None
    
    def _remove_from_db(self, key: str):
        """Remove entry from database"""
        try:
            db_file = CACHE_DB_PATH / "cache.db"
            conn = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM cache_entries WHERE key = ?', (key,))
            conn.commit()
            conn.close()
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.warning(f"SQLite error removing {key}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error removing {key}: {type(e).__name__}: {e}")
    
    def clear(self):
        """Clear all cache entries"""
        with self.lock:
            self.cache.clear()
            try:
                db_file = CACHE_DB_PATH / "cache.db"
                if db_file.exists():
                    conn = sqlite3.connect(str(db_file), check_same_thread=False)
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM cache_entries')
                    conn.commit()
                    conn.close()
            except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                logger.warning(f"SQLite error clearing cache: {e}")
            except Exception as e:
                logger.warning(f"Unexpected error clearing cache: {type(e).__name__}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.lock:
            valid_count = sum(1 for entry in self.cache.values() if self._is_valid(entry))
            return {
                'total_entries': len(self.cache),
                'valid_entries': valid_count,
                'expired_entries': len(self.cache) - valid_count
            }


# Global cache instance
_cache_instance = None


def get_cache(default_ttl: int = 600) -> CacheService:
    """Get or create the global cache instance"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = CacheService(default_ttl_seconds=default_ttl)
    return _cache_instance


def reset_cache():
    """Reset the global cache instance"""
    global _cache_instance
    _cache_instance = None

