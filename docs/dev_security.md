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
| Signing key | EC (ECDSA) | Authenticate requests via digital signatures |
| Encryption key | RSA | Encrypt data object content |
| Communication key | Curve25519 | Encrypt P2P communication channels |

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
- **Tamper-proof**: Signature covers method, URL, and request body
- **Replay-resistant within scope**: Different endpoints produce different signatures

## Transport Security

| Channel | Encryption | Notes |
|---------|-----------|-------|
| P2P (ZMQ) | Curve25519 (CurveZMQ) | Encrypted and authenticated when curve keys are configured |
| REST API | None (HTTP) | Relies on trusted network; TLS can be added via reverse proxy |

P2P communication between nodes uses CurveZMQ, which provides both encryption and authentication
at the transport level. Each node's communication key is part of its identity.

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
| Transport | Secure communication between nodes | CurveZMQ (P2P), HTTP/TLS (REST) |

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
