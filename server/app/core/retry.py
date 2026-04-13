"""
Retry utilities with exponential backoff for resilient API calls.
Phoenix pattern: graceful degradation with automatic retry.
"""
import logging
import time
from typing import Callable, TypeVar, Any, Optional, Type
from functools import wraps
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

T = TypeVar('T')


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,),
    on_retry: Optional[Callable[[int, Exception], None]] = None,
) -> Callable:
    """
    Decorator for retry with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        backoff_factor: Multiplier for delay on each retry
        exceptions: Tuple of exceptions to catch
        on_retry: Optional callback on retry
    
    Usage:
        @retry_with_backoff(max_retries=3, exceptions=(GoogleSheetsError, TimeoutError))
        def fetch_data():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            delay = initial_delay
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries:
                        logger.error(
                            f"{func.__name__} failed after {max_retries + 1} attempts: {e}",
                            exc_info=True
                        )
                        raise
                    
                    # Call retry callback if provided
                    if on_retry:
                        on_retry(attempt + 1, e)
                    else:
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1} failed: {type(e).__name__}: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                    
                    # Exponential backoff with jitter
                    delay = min(delay * backoff_factor, max_delay)
                    time.sleep(delay)
            
            # Shouldn't reach here but just in case
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


class CircuitBreaker:
    """
    Simple circuit breaker pattern for protecting against cascading failures.
    Tracks failure rate and opens circuit when threshold exceeded.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 300,
        name: str = "CircuitBreaker"
    ):
        """
        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            name: Name for logging
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.is_open = False
    
    def record_success(self) -> None:
        """Record a successful call"""
        self.failure_count = 0
        self.is_open = False
        logger.debug(f"{self.name}: Reset - operation succeeded")
    
    def record_failure(self, exception: Exception) -> None:
        """Record a failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            logger.warning(
                f"{self.name}: Circuit OPEN after {self.failure_count} failures. "
                f"Error: {type(exception).__name__}: {exception}"
            )
    
    def attempt_reset(self) -> bool:
        """Check if circuit can attempt recovery"""
        if not self.is_open:
            return True
        
        if self.last_failure_time:
            time_since_failure = datetime.now() - self.last_failure_time
            if time_since_failure.total_seconds() >= self.recovery_timeout:
                self.failure_count = 0
                self.is_open = False
                logger.info(f"{self.name}: Circuit CLOSED - attempting recovery")
                return True
        
        return False
    
    def call_allowed(self) -> bool:
        """Check if the circuit allows calls"""
        if not self.is_open:
            return True
        return self.attempt_reset()
    
    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Use as decorator"""
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            if not self.call_allowed():
                raise RuntimeError(f"{self.name}: Circuit breaker is OPEN")
            
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure(e)
                raise
        
        return wrapper
