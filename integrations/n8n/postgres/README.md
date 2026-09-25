# Finance operational Postgres schema

`001-finance-writer-lease.sql` is the fixed, versioned migration for the only
state that requires database compare-and-swap semantics. The lease workflow uses
the configured `n8n` database role from `DB_POSTGRESDB_USER`. The migration
grants that role `USAGE` on `finance_ops`, `SELECT`/`INSERT`/`UPDATE` on the
durable writer-effects table, and `EXECUTE` on the three `finance_ops` functions
after revoking their default `PUBLIC` privileges. It grants no direct access to
the lease table and no arbitrary DDL or delete privilege.

The `finance_ops.actual_writer_effects` table is keyed by `(resource_key,
outbox_id)` and stores the payload, target, period, lease tuple, and durable
writer state. `ISSUED`, `ACTUAL_OBSERVED`, and `OUTCOME_UNKNOWN` rows are
unresolved admission blockers. A fresh `PREPARED` row is admissible only for
the initial attempt (`attempt_count = 0`); otherwise, only an exact terminal
`VERIFIED`, `RECONCILED`, or `COMMITTED` row may admit a successor attempt. The
verified payload digest is required before a terminal state can be stored.

The lease contract requires these steps:

1. While an unexpired lease exists, return no row from the acquire function.
2. After each successful reacquisition, increment `fencing_token`.
3. Record `ISSUED` and call `assert_writer_lease`, then immediately call
   `actualBudget.import`.
4. Persist `OUTCOME_UNKNOWN` when the Actual result is unavailable.
5. After exact Actual readback and durable `COMMITTED` readback, release the
   exact `(resource_key, lease_id, fencing_token)`.
6. Before importing into the ledger, reject an expired or superseded token.

The migration remains a specification until these disposable checks pass:

- migration and restore
- concurrent acquisition
- kill-boundary recovery
- privilege enforcement
