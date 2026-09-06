# Security

Security fixes target the latest code on `main` during early development.
There is no long-term support branch or guaranteed response time.

## Reporting a vulnerability

If GitHub shows **Security → Report a vulnerability** for this repository, use
that private reporting channel. If it is unavailable, open an issue asking for
a private reporting channel, without exploit details, sensitive files or
credentials. Share the affected version, platform, impact and a minimal
synthetic reproduction once a private channel is established.

## Intended environment

The current CLI is for local, single-user authoring on a trusted filesystem.
It is not a sandbox, a multi-tenant service or an authorization boundary.
Operation adapters execute Python with the host user's permissions.

Managed assets are verified by hashes, and symlinks/reparse points are rejected.
These checks detect corruption and common path mistakes; they do not protect
against an attacker who can modify the workspace concurrently or replace both
data and its manifests. Do not expose this CLI as a network service without
separate access controls and isolation.

Only Windows and Linux publication primitives are implemented. Files are
flushed before publication, but full power-loss durability of filesystem
metadata and automatic recovery of interrupted requests are not provided.

Core 0.2 sessions use SQLite transactions and standard rollback-journal recovery
on reopen. This can recover uncommitted database writes; it does not recover an
interrupted audio action. Session queries require a writable local workspace so
SQLite can perform journal recovery if needed. Feedback source fields are caller
attribution, not authenticated user identities or proof of listening.

Core 0.3 adds explicit managed-job recovery after a worker exits, using local
OS file locks and verified immutable results. Never remove worker lock files
to bypass ownership. Cancellation is cooperative, not forced process-tree
termination. Legacy direct action claims are not reclaimed. These guarantees
do not extend to network filesystems, hostile clients or power-loss recovery.
