---
name: release-communications
description: Keep INPROFIC feature marketing, launch/social calendar, website-facing integration docs, and release highlights synchronized whenever user-visible capabilities change.
version: "1.0"
updated: 2026-09-20
---

# Release Communications

Apply this skill whenever a change adds, materially changes, renames, or retires a user-visible INPROFIC capability.

## Required update path

1. Implement and validate the product change first. Marketing copy must describe shipped behavior, never planned behavior as if it already exists.
2. Update the relevant product/API documentation and README/platform overview when the public behavior or integration contract changed.
3. Update `docs/feature_marketing_catalog.json`:
   - keep a stable `id` for an existing capability;
   - add a concise customer-facing name, audience and proof point;
   - set `highlight` to `NEW` for a newly shipped capability and record `added_on` as `YYYY-MM-DD`;
   - do not silently delete an old capability. Mark it `superseded` with its replacement when appropriate.
4. Ensure the marketing tooling is installed with `pip install -r requirements-marketing.txt`, then run `python scripts/generate_marketing_calendar.py` to refresh the CSV, Markdown summary, and `docs/INPROFIC_Q4_2026_Content_Calendar.xlsx` together.
5. Make additions obvious in calendar copy with `✨ NEW` until the release-highlight window expires. Repeated posts must change angle (education, workflow/problem, proof/control, ROI/readiness) rather than duplicate the same copy.
6. If an externally consumed Commerce/API feature changed, update both `docs/COMMERCE_INTEGRATION.md` and `docs/WEBSITE_COMMERCE_CHECKOUT_INTEGRATION.md` in the same change.

## Calendar rules

- Q4 2026 launch calendar covers every day from 1 October through 31 December.
- 1–7 October is the launch week.
- After launch, feature themes repeat from different angles across October, November and December.
- Every calendar row includes feature/theme, audience, funnel goal, format, organic/paid treatment, hook, message, CTA, creative brief and whether it is newly added.
- Do not make unsupported performance claims, fabricated customer counts, or guarantees.

## Product truth before promotion

Marketing automation never changes runtime behavior. Do not couple feature availability, synchronization, stock, finance, or audit persistence to whether a feature appears in this catalogue/calendar.
