"""
Security Module - 2x Security Hardening

Comprehensive security utilities:
- Path traversal prevention
- Input sanitization
- Rate limiting
- Session management
- Permission checking
- Audit logging

"""

import os
import re
import time
import hmac
import hashlib
import secrets
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from datetime import datetime, timedelta
from collections import defaultdict, deque
from threading import Lock

logger = logging.getLogger("sovereign.security")


# =============================================================================
# PATH SECURITY
# =============================================================================

class PathValidator:
    """
    Prevents path traversal attacks and validates filesystem operations.

    Usage:
        validator = PathValidator(allowed_roots=["/safe/dir"])
        safe_path = validator.validate("/safe/dir/subdir/file.txt")
    """

    def __init__(self, allowed_roots: List[str]):
        """
        Args:
            allowed_roots: List of allowed root directories (absolute paths)
        """
        self.allowed_roots = [Path(r).resolve() for r in allowed_roots]

    def validate(self, path: str) -> Path:
        """
        Validate and resolve a path, ensuring it stays within allowed roots.

        Args:
            path: Path to validate

        Returns:
            Resolved safe Path object

        Raises:
            SecurityError: If path escapes allowed roots or is invalid
        """
        try:
            # Resolve to absolute path, following symlinks
            resolved = Path(path).resolve(strict=False)

            # Check if path is within any allowed root
            for root in self.allowed_roots:
                try:
                    resolved.relative_to(root)
                    return resolved  # Path is safe
                except ValueError:
                    continue  # Not under this root, try next

            # Path not under any allowed root
            raise SecurityError(
                f"Path traversal attempt detected: {path}",
                details={"path": str(path), "resolved": str(resolved)}
            )

        except Exception as e:
            if isinstance(e, SecurityError):
                raise
            raise SecurityError(
                f"Invalid path: {path}",
                details={"error": str(e)}
            )

    def validate_filename(self, filename: str, max_length: int = 255) -> str:
        """
        Validate a filename for safety.

        Args:
            filename: Filename to validate
            max_length: Maximum allowed length

        Returns:
            Sanitized filename

        Raises:
            SecurityError: If filename is invalid
        """
        if not filename or len(filename) > max_length:
            raise SecurityError(f"Invalid filename length: {len(filename)}")

        # Block dangerous characters
        dangerous = set('<>:"|?*\x00')
        if any(c in filename for c in dangerous):
            raise SecurityError(f"Dangerous characters in filename: {filename}")

        # Block directory traversal
        if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
            raise SecurityError(f"Path traversal in filename: {filename}")

        return filename


# =============================================================================
# INPUT SANITIZATION
# =============================================================================

class InputSanitizer:
    """
    Sanitizes and validates user inputs.

    Usage:
        sanitizer = InputSanitizer()
        safe_text = sanitizer.sanitize_text(user_input)
    """

    # Dangerous patterns
    SQL_INJECTION_PATTERNS = [
        r"('\s*(OR|AND)\s*'?1'?\s*=\s*'?1)",
        r"(;\s*DROP\s+TABLE)",
        r"(--\s*$)",
        r"(/\*.*\*/)",
    ]

    COMMAND_INJECTION_PATTERNS = [
        r"(;\s*rm\s+-rf)",
        r"(\|\s*bash)",
        r"(`.*`)",
        r"(\$\(.*\))",
    ]

    def __init__(self, max_length: int = 10000):
        self.max_length = max_length
        self.sql_regex = [re.compile(p, re.IGNORECASE) for p in self.SQL_INJECTION_PATTERNS]
        self.cmd_regex = [re.compile(p, re.IGNORECASE) for p in self.COMMAND_INJECTION_PATTERNS]

    def sanitize_text(self, text: str, allow_newlines: bool = True) -> str:
        """
        Sanitize text input, removing dangerous patterns.

        Args:
            text: Input text
            allow_newlines: Whether to allow newline characters

        Returns:
            Sanitized text

        Raises:
            SecurityError: If dangerous patterns detected
        """
        if not isinstance(text, str):
            raise SecurityError(f"Expected string, got {type(text)}")

        if len(text) > self.max_length:
            raise SecurityError(f"Input too long: {len(text)} > {self.max_length}")

        # Check for SQL injection
        for pattern in self.sql_regex:
            if pattern.search(text):
                raise SecurityError("Potential SQL injection detected")

        # Check for command injection
        for pattern in self.cmd_regex:
            if pattern.search(text):
                raise SecurityError("Potential command injection detected")

        # Remove null bytes
        text = text.replace('\x00', '')

        # Optionally remove newlines
        if not allow_newlines:
            text = text.replace('\n', ' ').replace('\r', ' ')

        return text

    def sanitize_dict(self, data: Dict[str, Any], allowed_keys: Optional[Set[str]] = None) -> Dict[str, Any]:
        """
        Sanitize dictionary inputs.

        Args:
            data: Input dictionary
            allowed_keys: Optional set of allowed keys

        Returns:
            Sanitized dictionary

        Raises:
            SecurityError: If validation fails
        """
        if not isinstance(data, dict):
            raise SecurityError(f"Expected dict, got {type(data)}")

        sanitized = {}
        for key, value in data.items():
            # Validate key
            if allowed_keys and key not in allowed_keys:
                continue  # Skip unknown keys

            # Sanitize value
            if isinstance(value, str):
                sanitized[key] = self.sanitize_text(value)
            elif isinstance(value, (int, float, bool, type(None))):
                sanitized[key] = value
            elif isinstance(value, dict):
                sanitized[key] = self.sanitize_dict(value, allowed_keys)
            elif isinstance(value, list):
                sanitized[key] = [self.sanitize_text(v) if isinstance(v, str) else v for v in value]
            else:
                logger.warning(f"Skipping unsupported type for key {key}: {type(value)}")

        return sanitized


# =============================================================================
# RATE LIMITING
# =============================================================================

@dataclass
class RateLimit:
    """Configuration for rate limiting."""
    max_requests: int
    window_seconds: int
    burst_size: int = 0  # Allow brief bursts

    def __post_init__(self):
        if self.burst_size == 0:
            self.burst_size = self.max_requests


class RateLimiter:
    """
    Token bucket rate limiter.

    Usage:
        limiter = RateLimiter()
        limiter.add_limit("tool_call", RateLimit(max_requests=100, window_seconds=60))
        limiter.check("tool_call", "user123")
    """

    def __init__(self):
        self.limits: Dict[str, RateLimit] = {}
        self.buckets: Dict[str, Dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
        self.lock = Lock()

    def add_limit(self, operation: str, limit: RateLimit) -> None:
        """Add or update a rate limit."""
        self.limits[operation] = limit

    def check(self, operation: str, identifier: str) -> None:
        """
        Check if operation is allowed under rate limit.

        Args:
            operation: Operation type (e.g., "tool_call", "file_write")
            identifier: Unique identifier (e.g., session_id, user_id)

        Raises:
            SecurityError: If rate limit exceeded
        """
        if operation not in self.limits:
            return  # No limit configured

        limit = self.limits[operation]
        now = time.time()
        cutoff = now - limit.window_seconds

        with self.lock:
            # Get request timestamps for this identifier
            timestamps = self.buckets[operation][identifier]

            # Remove old requests outside window
            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()

            # Check if limit exceeded
            if len(timestamps) >= limit.max_requests:
                raise SecurityError(
                    f"Rate limit exceeded for {operation}",
                    details={
                        "operation": operation,
                        "limit": limit.max_requests,
                        "window": limit.window_seconds,
                        "current": len(timestamps)
                    }
                )

            # Add current request
            timestamps.append(now)


# =============================================================================
# SESSION MANAGEMENT
# =============================================================================

@dataclass
class Session:
    """Secure session with expiry."""
    session_id: str
    created_at: datetime
    expires_at: datetime
    data: Dict[str, Any] = field(default_factory=dict)
    last_activity: datetime = field(default_factory=datetime.utcnow)

    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.utcnow() > self.expires_at

    def refresh(self, ttl_seconds: int = 3600) -> None:
        """Refresh session expiry."""
        self.last_activity = datetime.utcnow()
        self.expires_at = self.last_activity + timedelta(seconds=ttl_seconds)


class SessionManager:
    """
    Secure session management with cryptographic tokens.

    Usage:
        manager = SessionManager()
        session_id = manager.create_session(ttl_seconds=3600)
        session = manager.get_session(session_id)
    """

    def __init__(self, secret_key: Optional[bytes] = None):
        self.secret_key = secret_key or secrets.token_bytes(32)
        self.sessions: Dict[str, Session] = {}
        self.lock = Lock()

    def create_session(self, ttl_seconds: int = 3600, data: Optional[Dict] = None) -> str:
        """
        Create a new secure session.

        Args:
            ttl_seconds: Time to live in seconds
            data: Optional session data

        Returns:
            Cryptographically secure session ID
        """
        session_id = self._generate_secure_id()
        now = datetime.utcnow()

        session = Session(
            session_id=session_id,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            data=data or {}
        )

        with self.lock:
            self.sessions[session_id] = session

        return session_id

    def get_session(self, session_id: str) -> Session:
        """
        Get and validate a session.

        Args:
            session_id: Session identifier

        Returns:
            Session object

        Raises:
            SecurityError: If session invalid or expired
        """
        with self.lock:
            if session_id not in self.sessions:
                raise SecurityError("Invalid session ID")

            session = self.sessions[session_id]

            if session.is_expired():
                del self.sessions[session_id]
                raise SecurityError("Session expired")

            return session

    def destroy_session(self, session_id: str) -> None:
        """Destroy a session."""
        with self.lock:
            self.sessions.pop(session_id, None)

    def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count removed."""
        with self.lock:
            expired = [sid for sid, s in self.sessions.items() if s.is_expired()]
            for sid in expired:
                del self.sessions[sid]
            return len(expired)

    def _generate_secure_id(self) -> str:
        """Generate cryptographically secure session ID."""
        random_bytes = secrets.token_bytes(32)
        timestamp = str(time.time()).encode()

        # HMAC for integrity
        hmac_digest = hmac.new(self.secret_key, random_bytes + timestamp, hashlib.sha256).digest()

        # Combine and encode
        combined = random_bytes + hmac_digest[:16]
        return secrets.token_urlsafe(len(combined))[:48]


# =============================================================================
# PERMISSION CHECKING
# =============================================================================

class PermissionChecker:
    """
    File operation permission checker.

    Usage:
        checker = PermissionChecker(allowed_operations={'read', 'write'})
        checker.check_permission('/path/to/file', 'write')
    """

    def __init__(self, allowed_operations: Optional[Set[str]] = None):
        self.allowed_operations = allowed_operations or {'read', 'write', 'delete'}

    def check_permission(self, path: str, operation: str) -> None:
        """
        Check if operation is permitted on path.

        Args:
            path: File path
            operation: Operation type ('read', 'write', 'delete')

        Raises:
            SecurityError: If permission denied
        """
        if operation not in self.allowed_operations:
            raise SecurityError(f"Operation not allowed: {operation}")

        path_obj = Path(path)

        # Check if path exists for read/delete
        if operation in {'read', 'delete'} and not path_obj.exists():
            raise SecurityError(f"Path does not exist: {path}")

        # Check write permissions
        if operation == 'write':
            parent = path_obj.parent
            if not os.access(parent, os.W_OK):
                raise SecurityError(f"No write permission: {path}")

        # Check read permissions
        if operation == 'read':
            if not os.access(path, os.R_OK):
                raise SecurityError(f"No read permission: {path}")


# =============================================================================
# CUSTOM EXCEPTIONS
# =============================================================================

class SecurityError(Exception):
    """Security violation detected."""

    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.utcnow().isoformat()

        # Log security event
        logger.error(
            f"SECURITY VIOLATION: {message}",
            extra={"details": self.details, "timestamp": self.timestamp}
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "error": "SecurityError",
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp
        }


# =============================================================================
# AUDIT LOGGING
# =============================================================================

class PersistentAuditLog:
    """
    Append-only audit log with hash chaining.

    Usage:
        audit = PersistentAuditLog("/path/to/audit.jsonl")
        audit.log("action", "actor", {"detail": "value"})
    """

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.lock = Lock()
        self.last_hash = self._load_last_hash()

        # Ensure log file exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.touch()

    def log(self, action: str, actor: str, details: Dict[str, Any]) -> str:
        """
        Append entry to audit log with hash chaining.

        Args:
            action: Action performed
            actor: Who performed it
            details: Additional details

        Returns:
            Entry hash
        """
        timestamp = datetime.utcnow().isoformat()

        # Create entry
        entry = {
            "timestamp": timestamp,
            "action": action,
            "actor": actor,
            "details": details,
            "previous_hash": self.last_hash
        }

        # Compute hash
        entry_str = f"{timestamp}:{action}:{actor}:{str(details)}:{self.last_hash}"
        entry_hash = hashlib.sha256(entry_str.encode()).hexdigest()
        entry["entry_hash"] = entry_hash

        # Write to file (append-only)
        with self.lock:
            import json
            with open(self.log_path, 'a') as f:
                f.write(json.dumps(entry) + '\n')

            self.last_hash = entry_hash

        return entry_hash

    def _load_last_hash(self) -> str:
        """Load the last hash from the log file."""
        if not self.log_path.exists():
            return "genesis"

        try:
            import json
            with open(self.log_path, 'r') as f:
                lines = f.readlines()
                if lines:
                    last_entry = json.loads(lines[-1])
                    return last_entry.get("entry_hash", "genesis")
        except Exception as e:
            logger.warning(f"Could not load last hash: {e}")

        return "genesis"


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'PathValidator',
    'InputSanitizer',
    'RateLimiter',
    'RateLimit',
    'SessionManager',
    'Session',
    'PermissionChecker',
    'PersistentAuditLog',
    'SecurityError',
]
