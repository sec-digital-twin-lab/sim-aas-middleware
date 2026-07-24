# Upgrading

## 5.0.0

Breaking change. The on-disk state from previous major versions is not compatible.

Before starting a node on 5.0.0 for the first time:

1. **Stop the node.**
2. **Wipe the NodeDB.** Delete the SQLite file the node uses (path is configured by the operator; default is alongside the keystore). Identity records in older databases are signed without the `simaas-identity:v1:` domain prefix and without the TLS cert field, and will fail `verify_integrity()` on load.
3. **Keep the keystore as-is.** The keystore re-signs the local identity on the next load — no operator step needed. The new signature uses the `simaas-identity:v1:` domain prefix.
4. **Rejoin the network.** Start the node and let it run its normal P2P join against a boot peer. The network rediscovers itself.

There is no migration script. The cost of a domain-prefixed signing scheme is one-time state wipe; the benefit is that old signatures can never be replayed against the new verifier.

### Operator-visible env vars introduced in 5.0.0

These are all optional; defaults match prior behavior. Out-of-range values fall back to the default with a `log.warning('config', ...)`.

- `SIMAAS_TRUSTED_PROXIES` — comma-separated CIDR list. If set, the gateway honors `X-Forwarded-For` when the immediate peer is in the trust list. Default: empty (use `request.client.host` as before).
- `SIMAAS_P2P_HANDSHAKE_TIMEOUT_SECONDS` — TLS handshake deadline on the server-side accept loop. Default: `10`. Range: `[1, 300]`.
- `SIMAAS_P2P_MAX_CONNS` — concurrent connection cap. Default: `512`. Range: `[1, 65535]`.
- `SIMAAS_API_KEY_MAX_FAILS` / `SIMAAS_API_KEY_COOLDOWN_SECONDS` — API-key brute-force cooldown. Defaults: `5` / `900`. Ranges: `[1, 1000]` / `[1, 86400]`.
- `SIMAAS_API_KEY_FAILURES_MAX` — cap on the in-memory failure-tracking table. Default: `4096`. Range: `[64, 1_000_000]`.
- `SIMAAS_JOIN_MAX_PEERS` — peer discovery cap during P2P join. Default: `10000`. Range: `[1, 10_000_000]`.
- `SIMAAS_DOR_MAX_PARTS` — multipart upload chunk-count cap. Default: `100000`. Range: `[1, 10_000_000]`.
- `SIMAAS_SIG_WINDOW_SECONDS` — REST/namespace signed-request replay window. Default: `300`. Range: `[10, 3600]`.
- `SIMAAS_P2P_MAX_ATTACHMENT_BYTES` — inbound P2P attachment ceiling. Default: 100 GiB. Range: `[1 MiB, 1 TiB]`.
