"""Unit tests for simaas.core.logging module - new Logger infrastructure."""

import logging
import os
import re
import time
from unittest.mock import patch

import pytest

from simaas.core.logging import (
    Logger,
    RateLimiter,
    get_logger,
    ID_FIELDS,
    SENSITIVE_KEYS,
    REDACT_PATTERNS,
)


class TestIDShortening:
    """Tests for automatic ID shortening."""

    def test_shorten_long_id(self):
        """Long IDs should be shortened to first4..last4."""
        logger = Logger('test', 'test')
        assert logger._shorten_id('abcdefghijklmnop') == 'abcd..mnop'

    def test_preserve_short_id(self):
        """Short IDs (<=12 chars) should not be modified."""
        logger = Logger('test', 'test')
        assert logger._shorten_id('abc123') == 'abc123'
        assert logger._shorten_id('exactly12ch') == 'exactly12ch'

    def test_shorten_12_char_id(self):
        """12-char IDs should not be shortened."""
        logger = Logger('test', 'test')
        assert logger._shorten_id('123456789012') == '123456789012'

    def test_shorten_13_char_id(self):
        """13-char IDs should be shortened."""
        logger = Logger('test', 'test')
        assert logger._shorten_id('1234567890123') == '1234..0123'

    def test_id_fields_recognized(self):
        """Fields in ID_FIELDS should be auto-shortened."""
        logger = Logger('test', 'test')
        long_id = 'abcdefghijklmnopqrstuvwxyz'

        for field in ['job', 'proc', 'obj', 'user', 'peer', 'id', 'job_id', 'proc_id']:
            result = logger._process_value(field, long_id)
            assert result == 'abcd..wxyz', f"Field {field} should be shortened"

    def test_suffix_id_fields_recognized(self):
        """Fields ending in _id should be auto-shortened."""
        logger = Logger('test', 'test')
        long_id = 'abcdefghijklmnopqrstuvwxyz'

        result = logger._process_value('custom_id', long_id)
        assert result == 'abcd..wxyz'

    def test_non_id_fields_preserved(self):
        """Non-ID fields should not be shortened."""
        logger = Logger('test', 'test')
        long_value = 'abcdefghijklmnopqrstuvwxyz'

        result = logger._process_value('message', long_value)
        assert result == long_value


class TestKeyBasedRedaction:
    """Tests for key-based sensitive data redaction."""

    def test_password_redacted(self):
        """Password fields should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('password', 'secret123') == '***'

    def test_secret_redacted(self):
        """Secret fields should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('secret', 'mysecret') == '***'
        assert logger._process_value('client_secret', 'abc') == '***'

    def test_token_redacted(self):
        """Token fields should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('token', 'abc123') == '***'
        assert logger._process_value('access_token', 'xyz') == '***'

    def test_key_redacted(self):
        """Key fields should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('api_key', 'abc') == '***'
        assert logger._process_value('apikey', 'abc') == '***'

    def test_credential_redacted(self):
        """Credential fields should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('credential', 'abc') == '***'
        assert logger._process_value('credentials', 'abc') == '***'

    def test_private_redacted(self):
        """Private fields should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('private', 'abc') == '***'
        assert logger._process_value('private_key', 'abc') == '***'

    def test_case_insensitive(self):
        """Key matching should be case-insensitive."""
        logger = Logger('test', 'test')
        assert logger._process_value('PASSWORD', 'abc') == '***'
        assert logger._process_value('Password', 'abc') == '***'
        assert logger._process_value('API_KEY', 'abc') == '***'


class TestPatternBasedRedaction:
    """Tests for pattern-based sensitive data redaction."""

    def test_private_key_redacted(self):
        """Private keys should be redacted."""
        logger = Logger('test', 'test')
        key = '-----BEGIN RSA PRIVATE KEY-----\nMIIE...'
        assert logger._process_value('data', key) == '***'

    def test_openssh_key_redacted(self):
        """OpenSSH private keys should be redacted."""
        logger = Logger('test', 'test')
        key = '-----BEGIN OPENSSH PRIVATE KEY-----\nb3Blbn...'
        assert logger._process_value('data', key) == '***'

    def test_openai_key_redacted(self):
        """OpenAI API keys should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('data', 'sk-abcdefghijklmnopqrstuvwx') == '***'

    def test_github_pat_redacted(self):
        """GitHub Personal Access Tokens should be redacted."""
        logger = Logger('test', 'test')
        pat = 'ghp_abcdefghijklmnopqrstuvwxyz1234567890'
        assert logger._process_value('data', pat) == '***'

    def test_github_oauth_redacted(self):
        """GitHub OAuth tokens should be redacted."""
        logger = Logger('test', 'test')
        token = 'gho_abcdefghijklmnopqrstuvwxyz1234567890'
        assert logger._process_value('data', token) == '***'

    def test_slack_token_redacted(self):
        """Slack tokens should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('data', 'xoxb-abc123') == '***'
        assert logger._process_value('data', 'xoxp-abc123') == '***'

    def test_aws_access_key_redacted(self):
        """AWS access keys should be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('data', 'AKIAIOSFODNN7EXAMPLE') == '***'

    def test_jwt_redacted(self):
        """JWTs should be redacted."""
        logger = Logger('test', 'test')
        jwt = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc'
        assert logger._process_value('data', jwt) == '***'

    def test_url_with_password_redacted(self):
        """URLs with embedded credentials should be redacted."""
        logger = Logger('test', 'test')
        url = 'https://user:password@example.com/path'
        assert logger._process_value('data', url) == '***'

    def test_safe_values_preserved(self):
        """Safe values should not be redacted."""
        logger = Logger('test', 'test')
        assert logger._process_value('data', 'hello world') == 'hello world'
        assert logger._process_value('data', '12345') == '12345'
        assert logger._process_value('url', 'https://example.com/path') == 'https://example.com/path'


class TestRateLimiter:
    """Tests for the RateLimiter class."""

    def test_first_message_logged(self):
        """First occurrence of a message should always be logged."""
        limiter = RateLimiter(window_seconds=60, max_duplicates=5)
        should_log, suppressed = limiter.should_log('test message')
        assert should_log is True
        assert suppressed == 0

    def test_within_limit_logged(self):
        """Messages within the limit should be logged."""
        limiter = RateLimiter(window_seconds=60, max_duplicates=5)
        for i in range(5):
            should_log, _ = limiter.should_log('test message')
            assert should_log is True

    def test_over_limit_suppressed(self):
        """Messages over the limit should be suppressed."""
        limiter = RateLimiter(window_seconds=60, max_duplicates=3)
        for i in range(3):
            limiter.should_log('test message')

        should_log, _ = limiter.should_log('test message')
        assert should_log is False

    def test_different_messages_independent(self):
        """Different messages should have independent counts."""
        limiter = RateLimiter(window_seconds=60, max_duplicates=2)

        # Max out message A
        limiter.should_log('message A')
        limiter.should_log('message A')
        should_log, _ = limiter.should_log('message A')
        assert should_log is False

        # Message B should still work
        should_log, _ = limiter.should_log('message B')
        assert should_log is True

    def test_window_expiration(self):
        """Messages should be allowed again after window expires."""
        limiter = RateLimiter(window_seconds=1, max_duplicates=1)

        # First message
        should_log, _ = limiter.should_log('test')
        assert should_log is True

        # Second message (suppressed)
        should_log, _ = limiter.should_log('test')
        assert should_log is False

        # Wait for window to expire
        time.sleep(1.1)

        # Should be allowed again with suppressed count
        should_log, suppressed = limiter.should_log('test')
        assert should_log is True
        assert suppressed >= 1

    def test_cleanup_expired(self):
        """Expired entries should be cleaned up."""
        limiter = RateLimiter(window_seconds=1, max_duplicates=5)
        limiter.should_log('test')
        assert len(limiter._messages) == 1

        time.sleep(1.1)
        limiter.cleanup_expired()
        assert len(limiter._messages) == 0


class TestLoggerFormat:
    """Tests for log message formatting."""

    def test_basic_format(self):
        """Test basic message format without kwargs."""
        logger = Logger('test.logger', 'test')
        result = logger._format('component', 'Test message', {})
        assert result == '[test.component] Test message'

    def test_format_with_kwargs(self):
        """Test message format with key-value pairs."""
        logger = Logger('test.logger', 'test')
        result = logger._format('component', 'Test message', {'name': 'val1', 'count': 'val2'})
        assert '[test.component] Test message |' in result
        assert 'name=val1' in result
        assert 'count=val2' in result

    def test_format_with_id_shortening(self):
        """Test that IDs in kwargs are shortened."""
        logger = Logger('test.logger', 'test')
        result = logger._format('deploy', 'Deploying', {'proc_id': 'abcdefghijklmnop'})
        assert 'proc_id=abcd..mnop' in result

    def test_format_with_sensitive_data(self):
        """Test that sensitive data is redacted."""
        logger = Logger('test.logger', 'test')
        result = logger._format('auth', 'Login', {'password': 'secret123'})
        assert 'password=***' in result
        assert 'secret123' not in result


class TestEnvironmentVariables:
    """Tests for environment variable handling."""

    def test_debug_mode_default_off(self):
        """Debug mode should be off by default."""
        with patch.dict(os.environ, {}, clear=True):
            logger = Logger('test', 'test')
            assert logger._debug_mode is False

    def test_debug_mode_enabled(self):
        """Debug mode should be enabled via SIMAAS_DEBUG."""
        with patch.dict(os.environ, {'SIMAAS_DEBUG': 'true'}):
            logger = Logger('test', 'test')
            assert logger._debug_mode is True

    def test_rate_limit_default_on(self):
        """Rate limiting should be on by default."""
        with patch.dict(os.environ, {}, clear=True):
            logger = Logger('test', 'test')
            assert logger._rate_limit_enabled is True

    def test_rate_limit_disabled(self):
        """Rate limiting should be disabled via SIMAAS_LOG_RATE_LIMIT."""
        with patch.dict(os.environ, {'SIMAAS_LOG_RATE_LIMIT': 'false'}):
            logger = Logger('test', 'test')
            assert logger._rate_limit_enabled is False

    def test_rate_limit_window_configurable(self):
        """Rate limit window should be configurable."""
        with patch.dict(os.environ, {'SIMAAS_LOG_RATE_LIMIT_WINDOW': '120'}):
            logger = Logger('test', 'test')
            assert logger._rate_limiter._window == 120

    def test_rate_limit_max_configurable(self):
        """Rate limit max should be configurable."""
        with patch.dict(os.environ, {'SIMAAS_LOG_RATE_LIMIT_MAX': '10'}):
            logger = Logger('test', 'test')
            assert logger._rate_limiter._max == 10


class TestDebugFreeform:
    """Tests for debug freeform logging."""

    def test_debug_without_kwargs(self, caplog):
        """Debug messages without kwargs should be plain."""
        logger = Logger('test', 'test')
        with caplog.at_level(logging.DEBUG):
            logger.debug('Simple debug message')

        assert 'Simple debug message' in caplog.text

    def test_debug_with_kwargs(self, caplog):
        """Debug messages with kwargs should include key=value pairs."""
        logger = Logger('test', 'test')
        with caplog.at_level(logging.DEBUG):
            logger.debug('Debug with context', var1='abc', var2=123)

        assert 'Debug with context |' in caplog.text
        assert 'var1=abc' in caplog.text
        assert 'var2=123' in caplog.text


class TestGetLogger:
    """Tests for the get_logger factory function."""

    def test_creates_logger(self):
        """get_logger should create a Logger instance."""
        logger = get_logger('test.module', 'test')
        assert isinstance(logger, Logger)

    def test_returns_same_instance(self):
        """get_logger should return the same instance for same name/subsystem."""
        logger1 = get_logger('test.module', 'subsys')
        logger2 = get_logger('test.module', 'subsys')
        assert logger1 is logger2

    def test_different_for_different_names(self):
        """get_logger should return different instances for different names."""
        logger1 = get_logger('module1', 'subsys')
        logger2 = get_logger('module2', 'subsys')
        assert logger1 is not logger2


class TestLoggerMethods:
    """Tests for Logger info/warning/error methods."""

    def test_info_logs_at_info_level(self, caplog):
        """info() should log at INFO level."""
        logger = Logger('test', 'test')
        with caplog.at_level(logging.INFO):
            logger.info('component', 'Test info message')

        assert 'INFO' in caplog.text
        assert '[test.component] Test info message' in caplog.text

    def test_warning_logs_at_warning_level(self, caplog):
        """warning() should log at WARNING level."""
        logger = Logger('test', 'test')
        with caplog.at_level(logging.WARNING):
            logger.warning('component', 'Test warning')

        assert 'WARNING' in caplog.text
        assert '[test.component] Test warning' in caplog.text

    def test_error_logs_at_error_level(self, caplog):
        """error() should log at ERROR level."""
        logger = Logger('test', 'test')
        with caplog.at_level(logging.ERROR):
            logger.error('component', 'Test error')

        assert 'ERROR' in caplog.text
        assert '[test.component] Test error' in caplog.text

    def test_error_with_exception_debug_off(self, caplog):
        """error() with exception should not include traceback when debug is off."""
        with patch.dict(os.environ, {'SIMAAS_DEBUG': 'false'}):
            logger = Logger('test', 'test')
            try:
                raise ValueError('test error')
            except ValueError as e:
                with caplog.at_level(logging.ERROR):
                    logger.error('component', 'Error occurred', exc=e)

        assert 'Traceback' not in caplog.text

    def test_error_with_exception_debug_on(self, caplog):
        """error() with exception should include traceback when debug is on."""
        with patch.dict(os.environ, {'SIMAAS_DEBUG': 'true'}):
            logger = Logger('test', 'test')
            try:
                raise ValueError('test error')
            except ValueError as e:
                with caplog.at_level(logging.ERROR):
                    logger.error('component', 'Error occurred', exc=e)

        assert 'Traceback' in caplog.text
        assert 'ValueError' in caplog.text


class TestRateLimitingIntegration:
    """Integration tests for rate limiting with Logger."""

    def test_warning_rate_limited(self, caplog):
        """WARNING level should be rate limited."""
        with patch.dict(os.environ, {'SIMAAS_LOG_RATE_LIMIT': 'true', 'SIMAAS_LOG_RATE_LIMIT_MAX': '2'}):
            logger = Logger('test', 'test')

            with caplog.at_level(logging.WARNING):
                for _ in range(5):
                    logger.warning('comp', 'Same warning')

            # Should only see 2 occurrences (rate limited)
            assert caplog.text.count('Same warning') == 2

    def test_info_not_rate_limited(self, caplog):
        """INFO level should not be rate limited."""
        with patch.dict(os.environ, {'SIMAAS_LOG_RATE_LIMIT': 'true', 'SIMAAS_LOG_RATE_LIMIT_MAX': '2'}):
            logger = Logger('test', 'test')

            with caplog.at_level(logging.INFO):
                for _ in range(5):
                    logger.info('comp', 'Same info')

            # All 5 should appear
            assert caplog.text.count('Same info') == 5

    def test_rate_limit_disabled(self, caplog):
        """With rate limiting disabled, all messages should be logged."""
        with patch.dict(os.environ, {'SIMAAS_LOG_RATE_LIMIT': 'false'}):
            logger = Logger('test', 'test')

            with caplog.at_level(logging.WARNING):
                for _ in range(5):
                    logger.warning('comp', 'Same warning')

            assert caplog.text.count('Same warning') == 5
