# Finance operational Postgres schema

`001-finance-writer-lease.sql` is the fixed, versioned migration for the only
state that requires database compare-and-swap semantics. The n8n database role
used by the lease workflow receives execute permission only on the three
`finance_ops` functions; it receives no arbitrary DDL or table-write privilege.

The acquire function returns no row while an unexpired lease exists. Every
successful reacquisition increments `fencing_token`. The workflow must call
`assert_writer_lease` immediately before `actualBudget.import`, and must release
the exact `(resource_key, lease_id, fencing_token)` after COMMITTED readback.
An expired or superseded token fails closed before an Actual write.

This migration is a specification until disposable migration, concurrency,
kill-boundary, privilege, and restore tests have produced fresh receipts.
