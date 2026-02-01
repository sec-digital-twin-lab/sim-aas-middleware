import hashlib
import logging
import os
import re
import time
import traceback
from logging.handlers import RotatingFileHandler
from threading import Lock
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import NoCredentialsError
from watchtower import CloudWatchLogHandler


# =============================================================================
# New Logger Infrastructure
# =============================================================================

# Fields that should have their values auto-shortened
ID_FIELDS = frozenset({
    'job', 'proc', 'obj', 'user', 'peer', 'id',
    'job_id', 'proc_id', 'obj_id', 'user_id', 'peer_id',
    'batch_id', 'container_id', 'aws_job_id', 'runner_id',
    'identity_id', 'owner_iid', 'user_iid', 'target_node_iid',
})

# Field names that indicate sensitive data (case-insensitive match)
SENSITIVE_KEYS = frozenset({
    'password', 'secret', 'token', 'key', 'credential', 'private',
    'api_key', 'apikey', 'access_key', 'secret_key', 'auth',
})

# Patterns that indicate sensitive values (regardless of key name)
REDACT_PATTERNS = [
    re.compile(r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'),  # Private keys
    re.compile(r'^sk-[a-zA-Z0-9]{20,}'),      # OpenAI, Stripe
    re.compile(r'^ghp_[a-zA-Z0-9]{36,}'),     # GitHub PAT
    re.compile(r'^gho_[a-zA-Z0-9]{36,}'),     # GitHub OAuth
    re.compile(r'^xox[baprs]-'),              # Slack
    re.compile(r'^AKIA[0-9A-Z]{16}'),         # AWS access key
    re.compile(r'^eyJ[a-zA-Z0-9_-]*\.eyJ'),   # JWT
    re.compile(r'://[^:]+:[^@]+@'),           # user:pass@host in URLs
]


class RateLimiter:
    """Suppress duplicate log messages within a time window."""

    def __init__(self, window_seconds: int = 60, max_duplicates: int = 5):
        self._window = window_seconds
        self._max = max_duplicates
        self._messages: Dict[str, Tuple[int, float]] = {}  # hash -> (count, first_seen)
        self._lock = Lock()

    def should_log(self, message: str) -> Tuple[bool, int]:
        """
        Check if a message should be logged.

        Returns:
            Tuple of (should_log, suppressed_count)
            - should_log: True if message should be logged
            - suppressed_count: Number of times this message was suppressed since last log
        """
        msg_hash = hashlib.md5(message.encode()).hexdigest()
        now = time.time()

        with self._lock:
            if msg_hash in self._messages:
                count, first_seen = self._messages[msg_hash]

                # Window expired - reset
                if now - first_seen >= self._window:
                    self._messages[msg_hash] = (1, now)
                    return True, count - 1 if count > 1 else 0

                # Within window
                if count < self._max:
                    self._messages[msg_hash] = (count + 1, first_seen)
                    return True, 0
                else:
                    self._messages[msg_hash] = (count + 1, first_seen)
                    return False, 0
            else:
                self._messages[msg_hash] = (1, now)
                return True, 0

    def cleanup_expired(self) -> None:
        """Remove expired entries from the cache."""
        now = time.time()
        with self._lock:
            expired = [h for h, (_, first_seen) in self._messages.items()
                       if now - first_seen >= self._window]
            for h in expired:
                del self._messages[h]


class Logger:
    """Smart logger with auto ID shortening, sensitive data redaction, and rate limiting.

    Target format: [subsystem.component] Human-readable sentence | key=value key=value
    """

    def __init__(self, name: str, subsystem: str):
        self._name = name
        self._subsystem = subsystem
        self._logger = logging.getLogger(name)
        self._rate_limiter = RateLimiter(
            window_seconds=int(os.environ.get('SIMAAS_LOG_RATE_LIMIT_WINDOW', '60')),
            max_duplicates=int(os.environ.get('SIMAAS_LOG_RATE_LIMIT_MAX', '5'))
        )
        self._rate_limit_enabled = os.environ.get('SIMAAS_LOG_RATE_LIMIT', 'true').lower() == 'true'
        self._debug_mode = os.environ.get('SIMAAS_DEBUG', 'false').lower() == 'true'

    def _shorten_id(self, id_value: str) -> str:
        """Shorten long IDs to first4..last4 format."""
        if isinstance(id_value, str) and len(id_value) > 12:
            return f"{id_value[:4]}..{id_value[-4:]}"
        return str(id_value)

    def _is_sensitive_key(self, key: str) -> bool:
        """Check if a key name indicates sensitive data."""
        key_lower = key.lower()
        return any(sensitive in key_lower for sensitive in SENSITIVE_KEYS)

    def _is_sensitive_value(self, value: str) -> bool:
        """Check if a value matches known sensitive data patterns."""
        if not isinstance(value, str):
            return False
        for pattern in REDACT_PATTERNS:
            if pattern.search(value):
                return True
        return False

    def _process_value(self, key: str, value: Any) -> str:
        """Process a value for logging - shorten IDs and redact sensitive data."""
        # Check for sensitive keys first
        if self._is_sensitive_key(key):
            return '***'

        # Convert to string for further processing
        str_value = str(value) if value is not None else 'None'

        # Check for sensitive value patterns
        if self._is_sensitive_value(str_value):
            return '***'

        # Check if this is an ID field that should be shortened
        key_lower = key.lower()
        if key_lower in ID_FIELDS or key_lower.endswith('_id'):
            return self._shorten_id(str_value)

        return str_value

    def _format(self, component: str, message: str, kwargs: dict) -> str:
        """Format a log message with prefix and key=value pairs."""
        prefix = f"[{self._subsystem}.{component}]"

        if kwargs:
            kv_pairs = ' '.join(f"{k}={self._process_value(k, v)}" for k, v in kwargs.items())
            return f"{prefix} {message} | {kv_pairs}"
        else:
            return f"{prefix} {message}"

    def _should_rate_limit(self, level: int) -> bool:
        """Check if rate limiting should be applied to this log level."""
        return self._rate_limit_enabled and level >= logging.WARNING

    def _log(self, level: int, component: str, message: str, exc: Optional[Exception] = None, **kwargs) -> None:
        """Internal logging method with rate limiting and formatting."""
        formatted = self._format(component, message, kwargs)

        # Apply rate limiting for WARNING and above
        if self._should_rate_limit(level):
            should_log, suppressed = self._rate_limiter.should_log(formatted)
            if not should_log:
                return
            if suppressed > 0:
                formatted = f"{formatted} (suppressed {suppressed} similar messages)"

        # Add exception traceback if in debug mode and exception provided
        if exc is not None and self._debug_mode:
            tb = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            formatted = f"{formatted}\n{tb}"

        self._logger.log(level, formatted)

    def info(self, component_or_message: str, message: str = None, **kwargs) -> None:
        """Log an info message.

        Can be called in two ways:
        - info(component, message, **kwargs) - structured format with [subsystem.component] prefix
        - info(message) - freeform format without component
        """
        if message is None:
            # Freeform mode: just a message string
            self._logger.info(component_or_message)
        else:
            self._log(logging.INFO, component_or_message, message, **kwargs)

    def warning(self, component_or_message: str, message: str = None, **kwargs) -> None:
        """Log a warning message.

        Can be called in two ways:
        - warning(component, message, **kwargs) - structured format
        - warning(message) - freeform format
        """
        if message is None:
            self._logger.warning(component_or_message)
        else:
            self._log(logging.WARNING, component_or_message, message, **kwargs)

    def error(self, component_or_message: str, message: str = None, exc: Optional[Exception] = None, **kwargs) -> None:
        """Log an error message, optionally including exception traceback in debug mode.

        Can be called in two ways:
        - error(component, message, exc=..., **kwargs) - structured format
        - error(message) - freeform format
        """
        if message is None:
            self._logger.error(component_or_message)
        else:
            self._log(logging.ERROR, component_or_message, message, exc=exc, **kwargs)

    def debug(self, message: str, **kwargs) -> None:
        """Log a debug message (freeform, no component required)."""
        if kwargs:
            kv_pairs = ' '.join(f"{k}={self._process_value(k, v)}" for k, v in kwargs.items())
            formatted = f"{message} | {kv_pairs}"
        else:
            formatted = message
        self._logger.debug(formatted)


# =============================================================================
# Logger registry and factory
# =============================================================================

_loggers: Dict[str, Logger] = {}
_loggers_lock = Lock()
_initialise_lock = Lock()

_default_format: str = '%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s'
_default_datefmt: str = '%Y-%m-%d %H:%M:%S'


def get_logger(
    name: str,
    subsystem: str,
    level: int = None,
    custom_log_path: str = None
) -> Logger:
    """Get or create a Logger instance.

    Args:
        name: The logger name (e.g., 'simaas.rti')
        subsystem: The subsystem tag (e.g., 'rti')
        level: Optional custom log level for this logger
        custom_log_path: Optional path to a custom log file for this logger

    Returns:
        A Logger instance
    """
    key = f"{name}:{subsystem}"
    if custom_log_path:
        # Custom log path means a unique logger instance
        key = f"{key}:{custom_log_path}"

    with _loggers_lock:
        if key not in _loggers:
            logger = Logger(name, subsystem)

            # Set custom level if provided
            if level is not None:
                logger._logger.setLevel(level)

            # Add custom file handler if provided
            if custom_log_path:
                file_handler = logging.FileHandler(custom_log_path)
                file_handler.setFormatter(logging.Formatter(_default_format, _default_datefmt))
                logger._logger.addHandler(file_handler)

            _loggers[key] = logger
        return _loggers[key]


def initialise(
    level: int = logging.INFO,
    log_path: str = None,
    console_log_enabled: bool = True,
    custom_format: str = None,
    custom_datefmt: str = None,
    max_bytes: int = 1*1024*1024,
    backup_count: int = 10,
    log_to_aws: bool = False
) -> None:
    """Initialise the root logger with handlers.

    Args:
        level: Default log level
        log_path: Path to log file (uses rotating file handler)
        console_log_enabled: Whether to log to console
        custom_format: Custom log format string
        custom_datefmt: Custom date format string
        max_bytes: Max bytes before log rotation
        backup_count: Number of backup log files to keep
        log_to_aws: Whether to enable AWS CloudWatch logging
    """
    with _initialise_lock:
        # Set formatting and use GMT/UTC timezone
        formatter = logging.Formatter(
            fmt=custom_format if custom_format else _default_format,
            datefmt=custom_datefmt if custom_datefmt else _default_datefmt
        )
        formatter.converter = time.gmtime

        # Set default log level on root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # Remove all existing handlers
        remove_all_handlers()

        # Add file handler if log path provided
        if log_path:
            file_handler = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup_count)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

        # Add console handler if enabled
        if console_log_enabled:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)

        # Add AWS CloudWatch handler if enabled
        if log_to_aws:
            required = ['SIMAAS_AWS_REGION', 'SIMAAS_AWS_LOG_GROUP_NAME', 'SIMAAS_AWS_TASK_ID']
            if not all(var in os.environ for var in required):
                root_logger.error(f"Required environment variables not defined: {required}")
            else:
                region_name = os.environ['SIMAAS_AWS_REGION']
                log_group_name = os.environ['SIMAAS_AWS_LOG_GROUP_NAME']
                log_stream_name = os.environ['SIMAAS_AWS_TASK_ID']
                boto3.setup_default_session(region_name=region_name)

                try:
                    cloudwatch_handler = CloudWatchLogHandler(
                        log_group_name=log_group_name,
                        log_stream_name=log_stream_name
                    )
                    cloudwatch_handler.setFormatter(formatter)
                    root_logger.addHandler(cloudwatch_handler)
                except NoCredentialsError:
                    root_logger.error("No credentials found for AWS CloudWatch.")


def remove_all_handlers() -> None:
    """Remove all handlers from the root logger."""
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:  # Copy list to avoid mutation during iteration
        handler.flush()
        handler.close()
        root_logger.removeHandler(handler)
