# Monthly statement delivery window

EI and Wio monthly pollers query the current delivery month, beginning at day 1
00:00 Asia/Dubai. The configured cycle day controls when polling may run, not
when delivered source messages become eligible. Wio messages delivered on days
1 or 2 must remain eligible when its first poll runs on day 3.

The window ends at the execution's exact timestamp. Poll-day eligibility,
deadlines and the operational `period_key` all use Dubai calendar boundaries.
`period_key` identifies the delivery/poll month; the statement's parsed dates
continue to determine its financial period. No statement dates are invented or
rewritten from the delivery month.

This prevents routine prior-month deliveries from being mixed into the current
monthly invocation. Distinct PDFs within the delivery window still fail closed
for review; this change does not select the first attachment, suppress an
ambiguous source, or reset a cursor. Late historical deliveries remain a reviewed
historical-ingestion concern rather than automatically shifting a live cycle.

Source acquisition receipts recorded the current EI delivery on September 1 and
Wio delivery late on September 2. Executable tests cover those UTC timestamps,
Dubai midnight, year rollover, the configured polling deadlines, and rejection
of an ordinary previous-month delivery. Real downstream acceptance remains a
separate production gate.
