# Cashback programme verification — 2026-09-04

This is a source verification record for the three seed programmes in
[`config/cashback-programs.json`](../config/cashback-programs.json). It uses
issuer-published pages and issuer PDFs retrieved on 2026-09-04. The seed JSON
keeps its cardholder-confirmed rates and routing unchanged; only the visible
estimate/provenance wording was clarified. All three programmes remain
`NON_AUTHORITATIVE` until claim-level evidence, effective dates, and content
digests are recorded in configuration.

The RAKBANK and Standard Chartered statement adapters are still interim
placeholders awaiting real statements. That does not block this cashback
review or the active ADCB history path.

## Findings

| Programme | Configured assumptions | Issuer evidence | Result |
| --- | --- | --- | --- |
| **RAKBANK World** | AED 10,000 tier; 10% on grocery, dining, and travel; caps AED 300/300/400; 1% standard retail capped AED 100; 3% e-wallet retail capped AED 150. | RAKBANK’s current World leaflet confirms the AED 10,000 minimum per statement cycle, the three 10% categories and those caps, plus 1% standard retail and 3% e-wallet retail. It also states that charity, government, bill, school/education, transit/transport, telecom, real-estate, and petrol/gas categories earn 0.25%; cashback has a AED 15,000 annual maximum divided into AED 400 travel, AED 300 grocery, AED 300 dining, and AED 250 other retail per month; redemption requires AED 300; qualifying category treatment is based on issuer-selected Mastercard MCCs. | **Partial match.** The headline rates, threshold, and monthly caps match. The seed does not model the 0.25% category band, AED 300 redemption minimum, explicit annual cap, or the issuer’s MCC list. Its broad text exclusions also do not encode the 0.25% exceptions. |
| **Standard Chartered Platinum X** | Spend tiers at AED 0/2,500/7,500/15,000; 3%/5%/10% on AED online, AED mobile-wallet POS, and foreign-currency buckets; caps at each tier of online 100/200/400, wallet 100/200/200, foreign 100/200/400. | Standard Chartered’s Platinum X page confirms the same four thresholds, rates, and bucket caps. Its programme terms define qualifying spend as AED online, non-AED foreign-currency, and AED mobile-wallet POS spend, and list exclusions including balance/payment plans, cash advances, card cheques, finance charges, all bank fees, and reversed merchant transactions. The terms also say transactions posted by the bank are used, with billing-date and merchant-claim timing that can move a transaction to the next statement. | **Rates/caps match; eligibility semantics are incomplete.** The seed lists only “issuer-designated ineligible transactions” and has no machine-readable treatment for the named exclusions, posting/billing cutover, or the bank’s card-status eligibility rules. |
| **Emirates Islamic Amazon** | A single uncapped configured 6% `EI_AMAZON` estimate for online Amazon transactions; tracking now identifies qualifying Prime membership, eligible Amazon.ae spend, Amazon Reward Points, and statement-only confirmation. | Emirates Islamic’s Amazon card page and reward-points guide state that rewards are Amazon Reward Points, not a statement cash credit. For Prime cardholders the published rates are 6% on-Amazon, 2% Amazon Ultra-Fast Grocery/gift cards, 2.5% international, 2% EEA/UK, up to 2% domestic off-Amazon, and 0.25% on listed specific categories. Non-Prime rates are 3%, 1%, 1%, 0.25%, up to 1%, and 0.25% respectively. Prime status is evaluated daily on the primary Amazon account. The page says there is no earning limit, while the guide limits monthly qualifying transactions to the assigned credit limit. | **Not sufficient for production accuracy.** The configured rate remains useful only as a cardholder-confirmed estimate. The engine has no Prime-status dimension and omits the other published spend categories/rates and the credit-limit/monthly qualification rule. |

## Official sources

All links below are issuer-controlled sources. “Retrieved” is the verification
date above; the pages do not by themselves establish that the seed’s
`effective_start` of 2026-08-01 is the correct start date.

### RAKBANK World

- [RAKBANK World Credit Card](https://www.rakbank.ae/en/cards/credit-cards/world-credit-card) — current product page: up to 10% on travel/hotel, supermarket, and dining, and up to 3% on retail.
- [RAKBANK World cashback leaflet (PDF)](https://www.rakbank.ae/globalassets/rakbank/all-pdfs/001---campaign/a-j00120-rak-cashback-leaflet---world_12062024-copy.pdf) — rates, caps, AED 10,000 statement-cycle minimum, 0.25% categories, annual maximum, redemption minimum, MCC and bank-channel terms. The PDF footer is `PPS–00559/V042026`; no separate effective date is printed.

### Standard Chartered Platinum X

- [Platinum X product and cashback page](https://www.sc.com/ae/credit-cards/platinum-x-cashback/) — tier thresholds, rates, and online/foreign/mobile-wallet caps.
- The same page’s [cashback programme terms](https://www.sc.com/ae/credit-cards/platinum-x-cashback/#terms-and-conditions) — qualifying transaction definition, exclusions, posting/billing timing, and redemption/account-status rules. The anchor is included for navigation; the terms are rendered in the page body.

### Emirates Islamic Amazon

- [Amazon Credit Card](https://www.emiratesislamic.ae/en/Personal-Banking/Cards/credit-cards/amazon-credit-card) — reward type, no-earning-limit headline, Prime/non-Prime framing, and welcome/eligibility information.
- [Guide to Earning Amazon Reward Points](https://www.emiratesislamic.ae/en/Personal-Banking/Cards/credit-cards/amazon-reward-points) — exact Prime/non-Prime category rates and the assigned-credit-limit rule.
- [Amazon card terms and conditions](https://www.emiratesislamic.ae/en/terms-and-conditions/amazon-cards) — issuer-specific terms linked from the product page; the product page also states that points can be forfeited for cancellation or prohibited/gaming behaviour.

## Rules-model implications

The v2 schema can express separate buckets by category, channel, currency,
foreign-only/base-currency-only status, rates, and per-bucket caps. Therefore
the RAKBANK 0.25% categories and the Emirates Islamic category bands are
representable after their normalized category/MCC mapping is agreed. The
schema cannot currently express a daily Prime-status condition, a reward
instrument that is Amazon-only points rather than statement cashback, an
annual aggregate cap, a redemption minimum, or issuer posting/cutover rules.

There is also a code-level safety limitation: `CardProgram.exclusions` is
loaded as descriptive metadata, but `finance_tracker.cashback.evaluate_card`
and `reward_total` enforce only bucket-level category/channel/currency filters.
An exclusion that exists only in the programme-level text list is therefore
not independently enforced by the calculator. The existing engine’s tier
threshold is computed from `CASHBACK_TOPICS` (purchase/refund/reversal), so it
must be reconciled with each issuer’s definition of “total spend” before any
programme is promoted to authoritative.

## Issue #87 production blockers

1. Obtain dated issuer evidence for each configured effective interval and
   attach claim paths and SHA-256 digests in `source_references`/`provenance`.
2. Decide whether RAKBANK’s 0.25% categories, annual cap, redemption minimum,
   bank channels, and MCC classifications are needed in live routing; if so,
   add normalized buckets/guards and tests.
3. Add an eligibility context for Emirates Islamic Prime status and model
   Amazon Reward Points separately from generic statement cashback, or narrow
   the programme’s documented scope explicitly to Prime on-Amazon tracking.
4. Encode or enforce named Standard Chartered and RAKBANK exclusions before
   allowing those programmes to drive production cashback decisions.
