# Logging Guide

The middleware uses a custom `Logger` class (`simaas/core/logging.py`) that provides structured
logging with automatic ID shortening, sensitive data redaction, and rate limiting.

## Basic Usage

```python
from simaas.core.logging import get_logger

log = get_logger('simaas.rti', 'rti')

# Structured: [subsystem.component] message | key=value
log.info('job', 'Job submitted to processor', job=job_id, proc=proc_id)
# Output: [rti.job] Job submitted to processor | job=550e..0000 proc=a3f2..b4c5

# Without component: [subsystem] message
log.warning('', 'Peer timed out', addr=peer_addr, elapsed_ms=5000)
# Output: [rti] Peer timed out | addr=192.168.1.5 elapsed_ms=5000

# With exception (traceback included only when SIMAAS_DEBUG=true)
log.error('job', 'Job execution failed', exc=e, job=job_id)

# Debug is freeform (no component required)
log.debug('Checking peer availability', peer=peer_id, attempt=3)
```

### Parameters

- **First argument** (`component`): Sub-tag appended to the subsystem. Use `''` to omit.
- **Second argument** (`message`): Human-readable sentence describing what happened.
- **Keyword arguments**: Structured data appended as `key=value` pairs after `|`.
- **`exc` keyword** (error only): Exception instance. Traceback is included when `SIMAAS_DEBUG=true`.

## Auto ID Shortening

Fields whose names match known ID patterns are automatically shortened to `first4..last4` format.
This keeps log lines readable without losing the ability to distinguish between IDs.

**Recognized ID fields:**

`job`, `proc`, `obj`, `user`, `peer`, `id`, `job_id`, `proc_id`, `obj_id`, `user_id`, `peer_id`,
`batch_id`, `container_id`, `aws_job_id`, `runner_id`, `identity_id`, `owner_iid`, `user_iid`,
`target_node_iid`

Any field name ending in `_id` is also auto-shortened.

```python
log.info('job', 'Started', job='550e8400e29b41d4a716446655440000')
# Output: [rti.job] Started | job=550e..0000
```

Values with 12 characters or fewer are not shortened.

## Sensitive Data Redaction

The logger automatically redacts sensitive data in two ways:

### Key-Based Redaction

Fields with names containing any of these strings (case-insensitive) are replaced with `***`:

`password`, `secret`, `token`, `key`, `credential`, `private`, `api_key`, `apikey`, `access_key`,
`secret_key`, `auth`

```python
log.info('auth', 'Login attempt', user='alice', password='hunter2')
# Output: [rti.auth] Login attempt | user=alice password=***
```

### Value Pattern Scanning

Regardless of field name, values matching these patterns are redacted:

| Pattern | Example |
|---------|---------|
| PEM private keys | `-----BEGIN RSA PRIVATE KEY-----` |
| OpenAI / Stripe keys | `sk-abc123...` |
| GitHub PATs | `ghp_abc123...` |
| GitHub OAuth tokens | `gho_abc123...` |
| Slack tokens | `xoxb-...` |
| AWS access keys | `AKIA...` |
| JWTs | `eyJhbG...eyJz...` |
| URLs with credentials | `https://user:pass@host` |

```python
log.info('git', 'Cloning repo', url='https://user:token123@github.com/org/repo')
# Output: [rti.git] Cloning repo | url=***
```

## Rate Limiting

WARNING and ERROR messages are rate-limited to prevent log spam from repeated failures. By
default, the same message is logged at most **5 times per 60-second window**. After that,
further duplicates are suppressed until the window expires.

When a suppressed message is finally logged again, the count of suppressed messages is appended:

```
[rti.job] Connection refused | addr=192.168.1.5 (suppressed 12 similar messages)
```

INFO and DEBUG messages are never rate-limited.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SIMAAS_DEBUG` | `false` | Include exception tracebacks in logs |
| `SIMAAS_LOG_RATE_LIMIT` | `true` | Enable rate limiting for WARNING/ERROR |
| `SIMAAS_LOG_RATE_LIMIT_WINDOW` | `60` | Rate limit window in seconds |
| `SIMAAS_LOG_RATE_LIMIT_MAX` | `5` | Max duplicates before suppression |

## Subsystem Tags

Use these subsystem tags when creating loggers:

| Tag | Scope |
|-----|-------|
| `p2p` | Peer-to-peer networking |
| `dor` | Data object repository |
| `rti` | Runtime infrastructure |
| `node` | Node lifecycle |
| `ns` | Namespace / resources |
| `auth` | Authentication / authorization |
| `cfg` | Configuration |
| `rest` | HTTP service |
| `ks` | Keystore |
| `cli` | Command-line interface |
| `core` | Core infrastructure |
