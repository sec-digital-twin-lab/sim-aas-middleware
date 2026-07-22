# Security Architecture

This document describes the security model, threat assumptions, and deployment boundaries of the
Sim-aaS Middleware.

## Deployment Model

The middleware is designed as **internal infrastructure**, deployed on trusted LANs or within cloud
VPCs. It is never directly exposed to the internet or end users.

The typical deployment uses a two-layer architecture:

```
End Users --> [Application Server] --> [Sim-aaS Middleware]
              OAuth2 / JWT / LDAP      Cryptographic identity
              User accounts            Keystores
              Internet-facing           Internal network only
```

The application server handles user authentication (OAuth2, JWT, LDAP, MFA) and maps authenticated
user accounts to middleware keystores and identities. The middleware handles model identity, access
control, data provenance, and job governance.

This pattern is analogous to how a database server sits behind an application server -- the database
has its own access control, but user-facing authentication is not its responsibility.

## Cryptographic Identity

The middleware uses **self-sovereign cryptographic identity** as its sole authentication mechanism.
There are no usernames, passwords, or sessions within the middleware itself.

Every identity consists of:

| Key | Algorithm | Purpose |
|-----|-----------|---------|
| Signing key | EC (ECDSA) | Authenticate REST requests and P2P attestations via digital signatures |
| Encryption key | RSA | Encrypt data object content |
| TLS certificate | Self-signed X.509 (RSA) | mTLS peer identity for P2P transport |

Identities are stored in **keystores** -- encrypted files protected by a user password (PBKDF2 key
derivation). The keystore's master key encrypts all other keys at rest.

### Identity Verification

Identity IDs are derived from the SHA-256 hash of the canonical identity representation. This makes
identities **self-verifying**: given an identity's public keys and profile, anyone can recompute the
ID and verify it matches. No central authority is needed.

This is inspired by cryptocurrency/blockchain patterns: identities are self-sovereign, actions
require private-key signatures, and verification uses public keys. This enables cross-organisation
trust without a central identity provider.

## Authentication Flow

Every REST API request is independently authenticated via cryptographic signatures. See
[Component Reference - Authentication](dev_components.md#authentication) for the signature
generation protocol and code examples.

Key properties:
- **Stateless**: No sessions or tokens to manage or revoke
- **Non-repudiable**: Signatures prove the request came from the keystore holder
- **Tamper-proof**: Signature covers method, URL, request body, and an `issued_at` timestamp
- **Time-bound**: Signatures are rejected outside a configurable window (default 300 s,
  set via `SIMAAS_SIG_WINDOW_SECONDS`); see `simaas/rest/auth.py::timestamp_within_window`
- **Replay-resistant within scope**: Different endpoints, bodies, and timestamps produce
  different signatures

## Transport Security

| Channel | Encryption | Notes |
|---------|-----------|-------|
| P2P (TCP) | TLS 1.3 (mTLS) | Each node's identity carries a self-signed TLS cert; the server rebuilds a per-accept trust bundle from `node.db.get_identities()` and pins client certs against it |
| REST API | None (HTTP) | Relies on trusted network; TLS can be added via reverse proxy |

P2P communication between nodes uses TLS 1.3 with mutual authentication (mTLS). Each node
presents its own self-signed cert (from `TLSCertAsset` in the keystore) and pins the peer's
expected cert as the trusted CA. Client certs are verified against the server's current
trust bundle, so unknown peers are rejected at the transport layer. Public protocols
(`@p2p_public_access`) accept anonymous clients that present no cert; authenticated protocols
(`@p2p_requires_authentication`) require a verified client cert. See
`simaas/p2p/base.py::build_client_ssl_context` and `build_server_ssl_context` for details.

Push-side ownership on data-object uploads follows the mTLS peer identity, unless a valid
**relay attestation** (signed by the runner or another intermediary) names a different
attester as the rightful owner. This lets the runner push through its custodian while
keeping ownership attributed to the runner rather than the relay. See
`simaas/dor/protocol.py::P2PPushDataObject.handle` and `P2PRelayPushDataObject.handle`.

REST API traffic is unencrypted by default. This is acceptable for the intended deployment model
(trusted internal network). For deployments that require encrypted REST traffic, place a TLS-
terminating reverse proxy (nginx, Caddy, etc.) in front of the middleware.

## Access Control

### Data Objects (DOR)

Data objects support owner-based access control:
- Every data object has an **owner** (an identity)
- Objects can be **access-restricted**, requiring explicit grants
- Ownership can be **transferred** to another identity
- Operations that modify objects require **ownership verification** via signature

### Processors (RTI)

- Processor deployment and undeployment can optionally require **node ownership** (strict mode)
- Job cancellation requires either **job ownership** or **node ownership**
- Job submission requires the processor to be deployed and not busy

## Separation of Concerns

| Layer | Responsibility | Auth Mechanism |
|-------|---------------|----------------|
| Application | User authentication, account management, sessions | OAuth2, JWT, LDAP |
| Middleware | Model identity, access control, data provenance, job governance | Cryptographic signatures |
| Transport | Secure communication between nodes | mTLS (P2P), HTTP/TLS (REST via reverse proxy) |

## Threat Model Assumptions

The middleware operates under these assumptions:

1. **Network is trusted**: Nodes communicate over a trusted LAN or VPC. Network-level attacks
   (eavesdropping, MITM) are mitigated by network-level controls, not the middleware.
2. **Keystores are protected**: Private keys are encrypted at rest. The password is the root of
   trust. Compromise of a keystore password compromises that identity.
3. **Application layer handles user-facing security**: The middleware does not validate end-user
   credentials, enforce password policies, or manage sessions.
4. **Nodes are operated by trusted parties**: A node operator has full access to their node's
   data and capabilities. Multi-tenancy isolation is at the identity level, not the node level.
