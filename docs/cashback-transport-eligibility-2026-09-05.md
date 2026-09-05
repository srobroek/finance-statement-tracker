# Transport and activity eligibility correction — 2026-09-05

The RAKBANK World issuer leaflet observed on 2026-09-05 defines its travel
reward category as flights and hotel stays. It separately places transit and
transport in the 0.25% category band. A broad Actual budget category therefore
cannot establish eligibility for the 10% travel bucket.

Source: [RAKBANK World leaflet, footer PPS–00559/V042026](https://www.rakbank.ae/globalassets/rakbank/all-pdfs/001---campaign/a-j00120-rak-cashback-leaflet---world_12062024-copy.pdf).
The observation date is not an assertion of the issuer's commencement date.

New automatic normalization maps Travel Transport to TRANSPORT and Travel
Activities to TRAVEL_ACTIVITY. Both are excluded from the current RAK retail
buckets and do not match its flight/hotel travel bucket. Auto-assignment now
enforces the same existing bucket eligibility filters as routing, including
when selecting a fallback. SC's channel-based eligibility remains independent.

No RAK 0.25% bucket is invented: its full MCC classification and cap treatment
are not implemented. Transport/activity spend remains recorded, with no
automatic RAK reward attributed by this limited model. Zero modeled reward is
not an assertion that the issuer pays zero. Programmes remain NON_AUTHORITATIVE.
Flights and Accommodation retain their AIRLINE and HOTEL mappings.

This changes automatic normalization/assignment only. It does not rewrite
persisted events, explicit manual reward-bucket tags, or finalized statement
evidence. The historical programme object remains unchanged. Explicitly
re-normalizing a historical source uses the shared corrected mapping; a
historical data migration or correction requires separate source reconciliation.

Regression tests cover normalized categories, explicit and fallback assignment,
routing ineligibility, ordinary retail and flight/hotel eligibility, preserved
purchase/refund spend, and manual bucket preservation.
