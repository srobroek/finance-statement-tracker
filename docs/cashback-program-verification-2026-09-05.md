# Cashback programme corrections observed 2026-09-05

The current configuration applies observed corrections from 2026-09-05. This
is the application's correction boundary, **not a claimed issuer commencement
date**. The cardholder-confirmed August version remains available through
2026-09-04 for historical replay. All programmes remain NON_AUTHORITATIVE.

The evidence receipt in `config/evidence/cashback-issuer-observations-2026-09-05.json`
records source URLs, observed claim paths and precisely labeled digest formats.
RAK/EI raw downloads returned HTTP 403; their recorded digests identify the
retrieval service's extracted response, not original issuer document bytes.
The SC digest covers downloaded HTML bytes. Digests establish the observed
content identity; they do not establish issuer effective dates. The profile's
claim dates describe when these observations were applied to configuration.

## Implemented corrections

- RAK grocery, dining and travel categories cannot be routed to standard or
  e-wallet retail rewards. Historical routes remain in the portable routing
  profile, but the current version's bucket exclusions reject those candidates.
  This preserves historical routing while preventing current overflow errors.
- SC rounds the complete monthly reward down to a whole AED after summing
  capped bucket rewards; individual buckets are not rounded separately.
- EI retains its conditional configured 6% rate. Without verified Prime status,
  purchase subtype and assigned credit limit, it exposes reward value as null,
  excludes unsupported routing, and still ingests/displays statement spend.
  The UI uses a compact unknown-eligibility status and no longer describes current eligible spend as unlimited. Numeric
  reward calculation explicitly rejects unverified eligibility, rather than
  silently returning zero.
- Undated programme loading now selects today's applicable version. Explicit
  historical dates select their historical versions.

## Unresolved facts

RAK's lower 0.25% category band, selected MCC mappings and banking-channel
exclusions are not fully modeled; neither are redemption restrictions and
cross-year cap accounting. SC named exclusions, posting/billing cutover,
qualifying-tier spend and account status require complete operational mapping.
FX costs are still cardholder configuration, not verified current tariff data.
EI needs dated membership and assigned-limit evidence before enabling its
conditional reward model; Amazon grocery/gift-card and other category bands
are outside the implemented scope. No programme is promoted to authoritative.
ADCB stays historical only, with final issuer zero-balance evidence unresolved.
