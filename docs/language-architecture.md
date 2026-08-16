# Language architecture

The repository intentionally uses two runtimes behind one Docker and scripting workflow.

- Python owns stable PDF extraction, bank adapters, rule semantics, evidence matching, reconciliation, cashback arithmetic, and the small cashback HTTP service. Rewriting these tested modules solely for language uniformity would add risk without removing a runtime from deployment because statement extraction remains Python-based.
- Node owns the official `@actual-app/api` boundary because Actual publishes and versions that API for JavaScript. The bridge is a narrow command-line adapter, not a second service process.
- JSON is the contract between them: normalized statement manifests, Actual import envelopes, cashback events, reconciliation payloads, and versioned configuration.

The Node bridge remains executable ESM (`.mjs`) because it is a small, process-per-command boundary and the Actual package already supplies TypeScript declarations. New pure bridge modules are covered by Node tests. If the bridge becomes a resident API or gains multiple external clients, migrate that surface to TypeScript with generated schemas; do not rewrite the Python domain engine merely to achieve a single-language repository.

Operationally, the split is hidden: Actual and Cashback Control are separate containers, monthly and hourly scripts call stable commands, and no Windows background process is required.
