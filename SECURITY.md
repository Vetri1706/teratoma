# Security and trust boundary

This repository is an experimental distributed-systems lab. It does not offer a
supported production release or a security-maintenance SLA.

The native adapter trusts its peers and clients. Peer routing IDs and
administrative RPCs are not authenticated, and transport is not encrypted.
CRC32 and SHA-256 checks detect accidental corruption; they do not authenticate
the sender. Keep experiments on loopback or an isolated trusted test network,
with disposable data. Do not expose the daemon to the public internet.

The simulator models selected failures. The formal developments prove statements
about their declared models. Neither establishes security against a malicious
peer, verifies a deployment, or certifies the Python/C implementation.

## Reporting

For ordinary reproducibility or correctness problems using synthetic data, open
a [bug report](https://github.com/Vetri1706/teratoma/issues/new?template=bug_report.yml).

For a sensitive vulnerability, check the repository's
[Security page](https://github.com/Vetri1706/teratoma/security) for a private
reporting option. If that option is unavailable, do not post exploit details or
private data publicly. Open a minimal issue requesting a private reporting
channel, without the sensitive details. This document does not claim that a
private disclosure channel or response schedule is currently available.

Useful reports identify the commit, affected component, exact reproduction,
expected boundary, and observed impact. Use synthetic credentials/data and
redact logs before sharing them.
