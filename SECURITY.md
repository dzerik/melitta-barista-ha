# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest release | Yes |
| Older releases | No |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue.
2. Email the maintainer or use [GitHub Security Advisories](https://github.com/dzerik/melitta-barista-ha/security/advisories/new).
3. Include a description of the vulnerability and steps to reproduce.

You will receive a response within 7 days. Fixes will be released as a patch version.

## BLE Security Note

This integration communicates with the coffee machine over Bluetooth Low Energy using AES/RC4 encryption with a shared key derived during pairing. The encryption key is specific to the BLE protocol and is not user-configurable.
