"""
Error Handling Module - 2x Error Resilience

Comprehensive error handling utilities:
- Structured exceptions
- Operation timeouts
- Retry logic with backoff
- Circuit breakers
- Error context propagation
- Graceful degradation

"""

import time
import logging
import asyncio
import functools
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from contextlib import contextmanager

logger = logging.getLogger("sovereign.errors")

T = TypeVar('T')


# =============================================================================
# STRUCTURED EXCEPTIONS
# =============================================================================

class ErrorSeverity(Enum):
    """Error severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Error categories for classification."""
    VALIDATION = "validation"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    RESOURCE = "resource"
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    LOGIC = "logic"
    EXTERNAL = "external"


@dataclass
class ErrorContext:
    """Rich error context for debugging."""
    category: ErrorCategory
    severity: ErrorSeverity
    operation: str
    details: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    stack_trace: Optional[str] = None
    recovery_suggestion: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "category": self.category.value,
            "severity": self.severity.value,
            "operation": self.operation,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "stack_trace": self.stack_trace,
            "recovery_suggestion": self.recovery_suggestion
        }


class SovereignError(Exception):
    """Base exception with rich context."""

    def __init__(
        self,
        message: str,
        context: Optional[ErrorContext] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.context = context
        self.cause = cause

        # Log error
        if context:
            log_level = {
                ErrorSeverity.INFO: logging.INFO,
                ErrorSeverity.WARNING: logging.WARNING,
                ErrorSeverity.ERROR: logging.ERROR,
                ErrorSeverity.CRITICAL: logging.CRITICAL
            }.get(context.severity, logging.ERROR)

            logger.log(log_level, f"{message}", extra={"context": context.to_dict()})

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        result = {
            "error": self.__class__.__name__,
            "message": self.message,
        }

        if self.context:
            result["context"] = self.context.to_dict()

        if self.cause:
            result["cause"] = str(self.cause)

        return result


class ValidationError(SovereignError):
    """Input validation failed."""
    pass


class PermissionError(SovereignError):
    """Permission denied."""
    pass


class TimeoutError(SovereignError):
    """Operation timed out."""
    pass


class ResourceError(SovereignError):
    """Resource exhausted or unavailable."""
    pass


class RetryExhaustedError(SovereignError):
    """Retry attempts exhausted."""
    pass


# =============================================================================
# TIMEOUT HANDLER
# =============================================================================

class TimeoutHandler:
    """
    Timeout wrapper for operations.

    Usage:
        handler = TimeoutHandler(timeout_seconds=30)
        result = handler.run(slow_function, arg1, arg2)
    """

    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

    def run(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Run function with timeout.

        Args:
            func: Function to run
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            TimeoutError: If operation times out
        """
        import signal

        def timeout_handler(signum, frame):
            raise TimeoutError(
                f"Operation timed out after {self.timeout_seconds}s",
                context=ErrorContext(
                    category=ErrorCategory.TIMEOUT,
                    severity=ErrorSeverity.ERROR,
                    operation=func.__name__,
                    details={"timeout": self.timeout_seconds},
                    recovery_suggestion="Increase timeout or optimize operation"
                )
            )

        # Set alarm
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(int(self.timeout_seconds))

        try:
            result = func(*args, **kwargs)
            signal.alarm(0)  # Cancel alarm
            return result
        except TimeoutError:
            raise
        except Exception as e:
            signal.alarm(0)
            raise
        finally:
            signal.signal(signal.SIGALRM, old_handler)

    async def run_async(self, coro: Callable[..., T], *args, **kwargs) -> T:
        """
        Run async function with timeout.

        Args:
            coro: Coroutine to run
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Coroutine result

        Raises:
            TimeoutError: If operation times out
        """
        try:
            return await asyncio.wait_for(
                coro(*args, **kwargs),
                timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                f"Async operation timed out after {self.timeout_seconds}s",
                context=ErrorContext(
                    category=ErrorCategory.TIMEOUT,
                    severity=ErrorSeverity.ERROR,
                    operation=coro.__name__ if hasattr(coro, '__name__') else 'async_operation',
                    details={"timeout": self.timeout_seconds},
                    recovery_suggestion="Increase timeout or optimize async operation"
                ),
                cause=e
            )


# =============================================================================
# RETRY LOGIC WITH EXPONENTIAL BACKOFF
# =============================================================================

@dataclass
class RetryConfig:
    """Configuration for retry logic."""
    max_attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retry_on: tuple = (Exception,)


class RetryHandler:
    """
    Retry logic with exponential backoff.

    Usage:
        handler = RetryHandler(RetryConfig(max_attempts=3))
        result = handler.run(flaky_function)
    """

    def __init__(self, config: RetryConfig):
        self.config = config

    def run(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Run function with retry logic.

        Args:
            func: Function to run
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            RetryExhaustedError: If all attempts fail
        """
        last_exception = None
        attempt = 0

        while attempt < self.config.max_attempts:
            attempt += 1

            try:
                result = func(*args, **kwargs)
                if attempt > 1:
                    logger.info(f"Operation succeeded on attempt {attempt}")
                return result

            except self.config.retry_on as e:
                last_exception = e
                logger.warning(
                    f"Attempt {attempt}/{self.config.max_attempts} failed: {e}"
                )

                if attempt < self.config.max_attempts:
                    delay = self._compute_delay(attempt)
                    logger.info(f"Retrying in {delay:.2f}s...")
                    time.sleep(delay)
                else:
                    break

        # All attempts exhausted
        raise RetryExhaustedError(
            f"Operation failed after {self.config.max_attempts} attempts",
            context=ErrorContext(
                category=ErrorCategory.LOGIC,
                severity=ErrorSeverity.ERROR,
                operation=func.__name__,
                details={
                    "attempts": self.config.max_attempts,
                    "last_error": str(last_exception)
                },
                recovery_suggestion="Check operation logic or increase retry attempts"
            ),
            cause=last_exception
        )

    def _compute_delay(self, attempt: int) -> float:
        """Compute delay for exponential backoff with jitter."""
        delay = min(
            self.config.initial_delay * (self.config.exponential_base ** (attempt - 1)),
            self.config.max_delay
        )

        if self.config.jitter:
            import random
            delay *= (0.5 + random.random())  # Add 0-50% jitter

        return delay


# =============================================================================
# CIRCUIT BREAKER
# =============================================================================

class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5  # Failures before opening
    recovery_timeout: float = 60.0  # Seconds before trying again
    success_threshold: int = 2  # Successes in half-open before closing


class CircuitBreaker:
    """
    Circuit breaker pattern for fault tolerance.

    Usage:
        breaker = CircuitBreaker()
        result = breaker.call(unreliable_service)
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Call function through circuit breaker.

        Args:
            func: Function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            ResourceError: If circuit is open
        """
        # Check circuit state
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                logger.info("Circuit breaker entering HALF_OPEN state")
            else:
                raise ResourceError(
                    "Circuit breaker is OPEN",
                    context=ErrorContext(
                        category=ErrorCategory.RESOURCE,
                        severity=ErrorSeverity.WARNING,
                        operation=func.__name__,
                        details={
                            "state": self.state.value,
                            "failure_count": self.failure_count
                        },
                        recovery_suggestion=f"Wait {self.config.recovery_timeout}s for circuit to reset"
                    )
                )

        # Attempt operation
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result

        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        """Handle successful operation."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info("Circuit breaker CLOSED after recovery")
        else:
            self.failure_count = max(0, self.failure_count - 1)

    def _on_failure(self) -> None:
        """Handle failed operation."""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning("Circuit breaker reopened during HALF_OPEN test")
        elif self.failure_count >= self.config.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(f"Circuit breaker OPENED after {self.failure_count} failures")

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if not self.last_failure_time:
            return True

        elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
        return elapsed >= self.config.recovery_timeout

    def reset(self) -> None:
        """Manually reset circuit breaker."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        logger.info("Circuit breaker manually reset")


# =============================================================================
# DECORATORS
# =============================================================================

def with_timeout(timeout_seconds: float = 30.0):
    """Decorator for timeout handling."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            handler = TimeoutHandler(timeout_seconds)
            return handler.run(func, *args, **kwargs)
        return wrapper
    return decorator


def with_retry(max_attempts: int = 3, initial_delay: float = 1.0):
    """Decorator for retry logic."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            config = RetryConfig(max_attempts=max_attempts, initial_delay=initial_delay)
            handler = RetryHandler(config)
            return handler.run(func, *args, **kwargs)
        return wrapper
    return decorator


def with_circuit_breaker(breaker: CircuitBreaker):
    """Decorator for circuit breaker."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return breaker.call(func, *args, **kwargs)
        return wrapper
    return decorator


# =============================================================================
# SAFE OPERATION WRAPPER
# =============================================================================

@contextmanager
def safe_operation(
    operation_name: str,
    category: ErrorCategory = ErrorCategory.LOGIC,
    severity: ErrorSeverity = ErrorSeverity.ERROR,
    reraise: bool = True
):
    """
    Context manager for safe operations with automatic error handling.

    Usage:
        with safe_operation("file_write", ErrorCategory.FILESYSTEM):
            write_to_file(path, data)
    """
    try:
        yield
    except Exception as e:
        context = ErrorContext(
            category=category,
            severity=severity,
            operation=operation_name,
            details={"error": str(e), "type": type(e).__name__}
        )

        logger.error(
            f"Operation '{operation_name}' failed: {e}",
            extra={"context": context.to_dict()},
            exc_info=True
        )

        if reraise:
            if isinstance(e, SovereignError):
                raise
            else:
                raise SovereignError(
                    f"Operation '{operation_name}' failed",
                    context=context,
                    cause=e
                )


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def validate_type(value: Any, expected_type: Type, field_name: str) -> None:
    """
    Validate type of a value.

    Args:
        value: Value to validate
        expected_type: Expected type
        field_name: Field name for error message

    Raises:
        ValidationError: If type doesn't match
    """
    if not isinstance(value, expected_type):
        raise ValidationError(
            f"Invalid type for {field_name}",
            context=ErrorContext(
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.ERROR,
                operation="type_validation",
                details={
                    "field": field_name,
                    "expected": expected_type.__name__,
                    "got": type(value).__name__
                },
                recovery_suggestion=f"Provide {field_name} as {expected_type.__name__}"
            )
        )


def validate_range(
    value: Union[int, float],
    min_value: Optional[Union[int, float]] = None,
    max_value: Optional[Union[int, float]] = None,
    field_name: str = "value"
) -> None:
    """
    Validate numeric range.

    Args:
        value: Value to validate
        min_value: Minimum allowed value
        max_value: Maximum allowed value
        field_name: Field name for error message

    Raises:
        ValidationError: If out of range
    """
    if min_value is not None and value < min_value:
        raise ValidationError(
            f"{field_name} below minimum",
            context=ErrorContext(
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.ERROR,
                operation="range_validation",
                details={"field": field_name, "value": value, "min": min_value},
                recovery_suggestion=f"Provide {field_name} >= {min_value}"
            )
        )

    if max_value is not None and value > max_value:
        raise ValidationError(
            f"{field_name} above maximum",
            context=ErrorContext(
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.ERROR,
                operation="range_validation",
                details={"field": field_name, "value": value, "max": max_value},
                recovery_suggestion=f"Provide {field_name} <= {max_value}"
            )
        )


def validate_not_empty(value: Union[str, list, dict], field_name: str) -> None:
    """
    Validate value is not empty.

    Args:
        value: Value to validate
        field_name: Field name for error message

    Raises:
        ValidationError: If empty
    """
    if not value:
        raise ValidationError(
            f"{field_name} cannot be empty",
            context=ErrorContext(
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.ERROR,
                operation="empty_validation",
                details={"field": field_name},
                recovery_suggestion=f"Provide non-empty {field_name}"
            )
        )


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Exceptions
    'SovereignError',
    'ValidationError',
    'PermissionError',
    'TimeoutError',
    'ResourceError',
    'RetryExhaustedError',
    # Enums
    'ErrorSeverity',
    'ErrorCategory',
    'CircuitState',
    # Data classes
    'ErrorContext',
    'RetryConfig',
    'CircuitBreakerConfig',
    # Handlers
    'TimeoutHandler',
    'RetryHandler',
    'CircuitBreaker',
    # Decorators
    'with_timeout',
    'with_retry',
    'with_circuit_breaker',
    # Context managers
    'safe_operation',
    # Validators
    'validate_type',
    'validate_range',
    'validate_not_empty',
]
