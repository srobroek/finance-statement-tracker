\if :{?commit_authorized}
\else
\set commit_authorized off
\endif
SELECT :'commit_authorized' = 'on' AS exact_commit_authorized \gset
\if :exact_commit_authorized
COMMIT;
\else
ROLLBACK;
\endif
