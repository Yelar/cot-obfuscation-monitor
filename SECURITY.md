# Security and secret handling

Never commit `.env`, Tinker checkpoint URIs, API tokens, private model outputs, or W&B credentials. `.env` and all runtime/output directories are ignored by default.

Before any public commit, run:

```bash
python -m cot_obfuscation_repro.audit --write-manifest
python -m cot_obfuscation_repro.audit --check-manifest
```

The audit scans for credential-shaped values, private workspace paths, and UUID-bearing Tinker URIs. It also verifies every bundled data hash.

If a credential is accidentally exposed, revoke it at the provider immediately, remove it from Git history with an appropriate history-rewrite tool, and rotate any downstream credentials that reused it. Merely deleting the latest file is insufficient.

Security concerns can be reported privately to the repository owner instead of filed with raw secrets in a public issue.
