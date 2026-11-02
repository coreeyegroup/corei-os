# F01 — Exposed identities: remediation runbook

The legacy public repository contains private keys and Vault initialization
material. Values are deliberately not reproduced here. Deleting files does not
revoke them; each identity must be revoked or replaced, then history purged.
Current validity of each item is **unverified** — treat all as compromised.

| # | Material | Legacy paths | Action | Done when |
| --- | --- | --- | --- | --- |
| 1 | ArgoCD repository SSH deploy key | `secrets/gitops/argocd-repo-key` (+ `.pub`) | Delete the deploy key in GitHub → `coreeyegroup/corei` → Settings → Deploy keys. Generate a new key only if a new GitOps setup needs one, stored in the secret manager. | Key absent from GitHub deploy keys; any old ArgoCD repo connection fails |
| 2 | Kafka CA private key and broker key | `state/security/kafka/ca.key`, `state/security/kafka/broker.key` | Stop trusting this CA everywhere. Issue a new CA and broker/client certificates for the new cluster (Delivery B). Never reuse the old CA. | No running system trusts the old CA |
| 3 | Vault root token + 5 unseal keys (3 copies) | `state/security/vault/init.json`, `state/security-backup/vault/init.json`, `infrastructure/state/stage-06/vault/vault-init-20260511-205557.json` | If the old Vault still runs: revoke the root token, rekey, and rotate every secret it stores (broker credentials, DB passwords). If it has been destroyed with the cluster: rotate every secret that was ever stored in it at its source (e.g. Upstox API secret, database passwords). | Every secret that lived in that Vault has a new value at its source |
| 4 | Broker API credentials (Upstox client id/secret, access tokens) | Stored via Vault/K8s secrets in the legacy cluster | Regenerate the Upstox API secret in the Upstox developer console; invalidate old access tokens. | Old secret rejected by Upstox |
| 5 | Database credentials | `market_data_persistence/app/main.py` (`coreeye:coreeye`), K8s manifests | New databases in Delivery B get generated credentials from the secret manager; old passwords are not reused. | No `coreeye:coreeye` anywhere |

## After revocation

1. Archive `coreeyegroup/corei` (read-only). If it must stay public, purge the
   paths above from history (`git filter-repo`) and force-push, then ask GitHub
   support to clear cached views. Treat purge as hygiene, not as revocation.
2. Keep any local backup tarball of the legacy repo encrypted and offline.
3. This repository enforces prevention: `scripts/repo_hygiene.py` and gitleaks
   run in pre-commit and CI and fail on key material, Vault init files and
   `.env` files.
