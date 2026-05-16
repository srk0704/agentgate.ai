# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in AgentGate, please report it
responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Email: security@agentgate.ai

We will acknowledge your report within 48 hours and provide a fix timeline
within 7 days for critical issues.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.7.x   | ✅        |
| < 0.7   | ❌        |

## Security Design

AgentGate is designed with these security principles:

- Fail-closed by default in production
- Every decision logged with full context
- No sensitive data stored in plaintext
- API key required for all state-changing endpoints in production mode
