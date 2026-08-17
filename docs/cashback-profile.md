# Cashback profile configuration

Cashback Control is a profile-driven application. The calculation engine and mobile UI do not require the bundled UAE portfolio: a deployment supplies one JSON profile through `CASHBACK_PROGRAM_CONFIG_PATH`.

The machine-readable contract is `config/cashback-profile-schema-v1.json`. The runtime also performs semantic validation across cards, tiers, buckets, routing policies, and decision-tree routes before starting.

## What belongs in a profile

- base currency, display name, locale, and short card names;
- card programme versions and effective dates;
- statement close day and forecast payment offset per card;
- flat or tiered reward rates;
- total-spend and bucket-spend tier requirements;
- reward caps or direct spend caps;
- category, channel, currency, domestic, foreign, and fallback bucket assignment;
- weekly or daily pace, with an independent weekly or cycle routing basis;
- near-full, minimum-risk, and final-week alert thresholds;
- Actual-category and merchant normalization fallbacks;
- purchase intents shown on the dashboard;
- routing policies, activation checks, rank groups, reasons, and decision-tree routes.

The code retains legacy `*_aed` input aliases for the original profile, but new public profiles should use currency-neutral fields such as `safety_target`, `minimum_spend`, `cashback_cap`, `spend_cap`, `decision_amount`, and `minimum_tolerance`.

AI policies can use `allowed_value_sources` instead of copying deployment values. `actual.categories` resolves from `ACTUAL_BOOTSTRAP_CONFIG_PATH`, and `cashback.buckets` resolves from `CASHBACK_PROGRAM_CONFIG_PATH`. This keeps model proposals constrained to the installed deployment without duplicating category or bucket lists.

## Tier requirements

Every tier has a `minimum_spend` shortcut. Additional requirements are ANDed:

```json
{
  "code": "PREMIUM",
  "minimum_spend": "1000",
  "requirements": [
    {
      "metric": "BUCKET_SPEND",
      "operator": "GTE",
      "bucket": "FOREIGN",
      "value": "200"
    }
  ],
  "rates": {"DOMESTIC": "0.03", "FOREIGN": "0.05"}
}
```

Supported metrics are `TOTAL_SPEND` and `BUCKET_SPEND`. Supported operators are `GTE`, `GT`, `LTE`, `LT`, and `EQ`. `target_tier` can name the tier whose rates should guide prospective routing before its requirements are secured.

## Bucket assignment and eligibility

Bucket eligibility controls whether a prospective purchase can earn from a bucket. `assignment` controls how imported transactions are classified into that bucket. Keeping them separate permits a physical filler transaction to be classified precisely while a routing policy may still consider broader channels for threshold progress.

```json
{
  "code": "ONLINE",
  "cashback_cap": "40",
  "channels": ["ONLINE"],
  "base_currency_only": true,
  "assignment": {
    "channels": ["ONLINE"],
    "base_currency_only": true
  }
}
```

An `assignment.fallback` bucket receives otherwise unmatched transactions only when the channel is known. Unknown-channel events remain reviewable rather than being silently forced into a bucket.

## Routing policies

Routes reference named policies. This keeps the decision tree data-driven instead of embedding a portfolio strategy in Python.

Policy checks:

- `bucket_open`
- `bucket_fits_purchase`
- `target_rate_positive`
- `net_value_positive`
- `target_unmet`
- `target_met`
- `pace_in`
- `pace_not_in`

`ranking.groups_by_pace` supplies the first sort key; lower ranks win. A route's numeric `priority` breaks ties before estimated value. Reasons are selected by pace status, with `*` as the fallback.

## Weekly pace

`pace.basis=WEEKLY` resets the displayed over/under comparison at each seven-day window from the card's statement-period start. The dashboard includes current week, weekly spend, weekly target, current-week variance, cycle variance, and both statuses.

`pace.routing_basis` is independent:

- `WEEKLY` makes routing respond to the current week's under/over status.
- `CYCLE` preserves cumulative threshold strategy while still displaying weekly under/over.

This separation prevents a front-loaded week from hiding a later weekly underspend, without forcing every portfolio to redirect purchases solely because the current week is quiet.

## Example profiles

The repository continuously validates and boots the app with four unrelated fictional deployments:

- `examples/cashback-profiles/flat-rate-usd.json`
- `examples/cashback-profiles/tiered-gbp.json`
- `examples/cashback-profiles/rotating-eur.json`
- `examples/cashback-profiles/requirements-cad.json`

They cover flat rate, category caps, multi-tier spend, rotating programme versions, foreign currency, custom statement dates, weekly routing, cycle routing, and compound tier requirements.

## Deploy

```bash
cp examples/cashback-profiles/flat-rate-usd.json deploy/cashback/cashback-profile.json
cd deploy/cashback
docker compose up -d
```

The Compose default stores SQLite under `deploy/cashback/data`. Set `CASHBACK_DATA_DIR` in the Compose `.env` file to use another persistent directory. Runtime secrets remain in the separate runtime environment file and must not be placed in the cashback profile.

The production workflow installs `config/cashback-programs.json` as the deployment's external `cashback-profile.json`. Updating a profile therefore does not require editing or rebuilding the Python engine, although the repository workflow still rebuilds and regression-tests the image for a controlled release.

The Compose deployment also mounts `actual-bootstrap.json`, `static-rules.json`, and `transaction-email-sources.json` externally. Together these files form a deployment bundle: accounts/categories, ordered rules, and provider notification contracts can all be replaced without modifying the image. A bank-specific parser remains a code adapter because parsing an issuer's evidence format is executable behaviour, not portfolio configuration.
