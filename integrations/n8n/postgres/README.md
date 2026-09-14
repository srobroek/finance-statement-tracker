# Finance operational Postgres schema

`001-finance-writer-lease.sql` is the fixed, versioned migration for the only
state that requires database compare-and-swap semantics. The lease workflow uses
the configured `n8n` database role from `DB_POSTGRESDB_USER`. The migration
grants that role `USAGE` on `finance_ops` and `EXECUTE` only on the three
`finance_ops` functions after revoking their default `PUBLIC` privileges. The
role receives no arbitrary DDL or table-write privilege.

The lease contract requires these steps:

1. While an unexpired lease exists, return no row from the acquire function.
2. After each successful reacquisition, increment `fencing_token`.
3. Call `assert_writer_lease`, then immediately call `actualBudget.import`.
4. After COMMITTED readback, release the exact `(resource_key, lease_id, fencing_token)`.
5. Before importing into the ledger, reject an expired or superseded token.

The migration remains a specification until these disposable checks pass:

- migration and restore
- concurrent acquisition
- kill-boundary recovery
- privilege enforcement
