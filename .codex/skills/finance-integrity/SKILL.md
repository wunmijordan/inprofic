---
name: finance-integrity
description: >
  Preserve StoreTrack finance, receivable, payable, CashAccount and
  FinancialTransaction integrity. Use when changes touch payments, sales,
  procurement, expenses, order completion, reversal, exports, or analytics.
version: "1.0"
updated: 2026-10-28
---

# Finance Integrity (StoreTrack)

## Principles

- `FinancialTransaction` describes actual money movement.
- `CashAccount` is the account/balance layer.
- Customer receivable and supplier payable status are settlement states, not
  production/procurement lifecycle status.
- Use `Decimal`; never floats for money.
- Historical money movements should normally be reversed with compensating
  entries rather than silently erased.
- A Sale generated from offcut allocation is a distinct sale/receivable from
  the original customer's Order.
- Production reversal must inspect later payments before automatically
  neutralizing a sale.

## Exports and analytics

Exports should use the same business-scoped query definition as the screen they
represent. Reversed/voided activity must be treated consistently between live
Finance tables and analytics.

## Books, tax and period scope

- Double-entry Books are derived from operational records by an explicit build /
  rebuild. Keep manual journals across rebuilds and reverse them with compensating
  journals. Do not describe the derived books as automatically current.
- Books and tax deliberately span Live and Backlog. Explicitly business-filtered
  `raw_objects` reads are appropriate for this cross-period scope; never drop the
  business filter when bypassing the ordinary period manager.
- Prices are VAT-inclusive. Product treatment and business registration/date/rate
  determine the extraction; input VAT is separately recorded. Settings are not
  automatically updated legal rules, and the VAT screen does not file a return.
- E-invoices are prepared per Sale, including Commerce-generated Sales once they
  exist. Keep checkout/pre-order state distinct from an issued invoice. Provider
  payload mapping and authority clearance must not be implied by a local invoice.
- Finance navigation and write controls follow Finance permissions/entitlements;
  chart edits and manual journals also retain their business-admin checks.

## Review questions

- Does this action move cash or only create a receivable/payable?
- Is a payment being counted twice through both status and ledger logic?
- If reversed, is there an audit-preserving compensating transaction?
- Could deleting an account/record break historical references?

## Subscription gateway rules

- StoreTrack currently supports Paystack and Monnify for SaaS subscription checkout.
- Provider secret credentials must remain server-side/environment-only.
- A browser callback or redirect is never sufficient proof of payment; use provider verification and keep `mark_payment_paid()` idempotent.
- Webhook signature validation precedes provider verification. The entitlement update happens only after authoritative amount/status verification.
- Yearly billing applies the founder-configured annual discount to the 12-month subscription total, including additional-service pricing.
- Customer commerce checkout payments may exist before any `CommerceIntake`; verified receipts remain financial truth even if intake materialization needs recovery. Only full verified settlement can create the intake.
