# Monthly acquisition boundary

The monthly cycle selects only a hash-verified, non-inline PDF from the frozen acquisition inventory. W01 retains the exact source message/attachment identity, archive item ID, source hash, and metadata classification for every archived attachment; W12 propagates those proofs without guessing a single item from a mixed archive.

A cycle accepts one unique statement PDF. Multiple notifications or attachments with identical verified bytes retain their separate archive receipts and are processed once. Multiple distinct PDF hashes fail before any Actual/Cashback write or cursor commit with `MONTHLY_STATEMENT_AMBIGUOUS_DISTINCT_PDFS_REVIEW_REQUIRED`. Review the source PDFs and bind the correct statement to the cycle; do not select the first archive item or discard the other evidence. Multi-month historical import remains a separate reviewed batch.

W22 downloads and hashes the selected archived PDF again before the statement pipeline. Cursor commit requires the same run/source's successful, readback-verified pipeline receipt. It sends W12 its existing archive-barrier hash, which is the acquisition state identity, and preserves the distinct pipeline receipt hash as separate evidence. Replaying an enumeration reconstructs typed proofs from the immutable attachment receipts; a cursor-only COMMIT retry does not trigger a new financial write.

The fixture tests execute exported nodes for proof propagation, same-byte source duplicates, inline/non-PDF exclusion, distinct-PDF ambiguity, failed byte readback, failed or foreign pipeline receipts, and the cursor hash contract. These tests are code evidence; real issuer input, n8n runtime execution, ledger/dashboard readback and scheduled restart acceptance remain required in issue #87.
