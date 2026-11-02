# R0 — Legacy source disposition

Legacy source: `github.com/coreeyegroup/corei` @ `a08758dc30a04919195ea59bc795a42ff3e1244d`
(246,914 tracked paths). Disposition follows section 11 of the assessment.
"Port" means re-implement against the new contracts with tests; nothing is
copied verbatim.

| Legacy asset | Path | Disposition | Target delivery | Notes |
| --- | --- | --- | --- | --- |
| Mission, phase separation, anchor principles | `docs/`, `knowledge/` | Retain as requirements | A→all | Convert to requirement→test matrix |
| Upstox V3 market-data parser | `core_platform/services/market_data_ingestion/app/connectors/upstox_connector.py` | Port with fixtures | C, F | Fix F09 (multi-instrument frames), F10 (index/LTPC), F11 (reconnect, bounded queue) |
| Kafka market-data path (ingest → normalize → persist → features) | `core_platform/services/market_data_*`, `feature_store_service` | Rebuild around reviewed logic | C | Per-key state (F13), versioned features (F14), real candles (F15) |
| Upstox REST execution adapter | `infrastructure/components/broker-adapter/app/adapters/upstox_adapter.py` | Quarantine; reimplement | F (read-only), H (orders) | Per-account sessions (F07); verify current official API first (C01) |
| Broker account/credential metadata schema | `infrastructure/components/broker-adapter/db/`, `infrastructure/migrations/postgres/011-012` | Reference for new migration | B | Credentials by reference only; never returned to UI (F08) |
| NSE session window logic | `platform/services/python-execution-service/app/services/trading_window_service.py` | Port as calendar reference data | C | Session eligibility separate from feed health (F26) |
| Go execution helpers | `services/execution/*` | Rebuild (Python first) | D | Fixed inputs, no wiring (F02, F04, F18, F19) |
| OMS / EMS / SOR / fill / reconciliation / copy-trade stubs | `infrastructure/components/*` | Replace | D, I | Constant responses (F20–F23, F40, F49) |
| Portfolio / replay concepts | `core_platform/portfolio`, `replay/` | Retain concept; rebuild | B, E | Reducer reused in replay (F22, F24) |
| Orbit UI | `ui/corei-orbit` | Refactor | G | Fix build, bind to real APIs, capability gating (F27–F30, F38) |
| corei-os UI, other UI generations, backups | `ui/corei-os`, `ui/corei-docs.backup`, `ui/status-server` | Retire | — | |
| Database migrations | `infrastructure/migrations`, `infrastructure/persistence` | Reconcile into one lineage | B | Competing definitions (F35, F43) |
| Kubernetes, ArgoCD, GitOps manifests | `kubernetes/`, `argocd/`, `gitops/`, `infrastructure/kubernetes` | Rebuild from environment model | G | Self-referencing root (F31), missing paths (F32), empty files (F33), `imagePullPolicy: Never` (F34) |
| Stage scripts and PASS reports | `infrastructure/phases/stage-*` | Archive as history | — | Not evidence (F25) |
| Runtime events, venvs, node_modules, tarballs | `runtime/`, `node_modules/`, `*.tar*` | Exclude | — | F37 |
| Committed keys, Vault init files | `secrets/`, `state/security*/` | Revoke and rotate; never import | A (R0) | See `secrets-remediation.md` (F01) |
