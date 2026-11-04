# System integrity fixes — 29 October 2026

## Procurement: request → approval → receipt

1. Procurement editors submit a **purchase request** with supplier, requested
   quantities and costs. New requests initially show Unpaid; record an advance
   only when money has actually moved.
2. Open **Review request** from Procurement. A business administrator approves
   each quantity. Approval can retain or reduce quantities, including zero to
   remove an item. Every reduction/removal requires a reason. It cannot add items,
   increase quantities or change costs; edit the unpaid request first instead.
3. Approval freezes the review, recording administrator, timestamp, requested
   quantities, approved quantities and reasons. At least one item must remain.
   Approval moves neither cash nor stock. Paid advances cannot exceed the new
   approved total; resolve any excess advance before approval.
4. **Receive** appears only after approval. Receipt applies approved positive
   quantities once, converts purchase units into usage units, records stock
   movements and dated cost snapshots, then marks Received. Repeated receipt
   submissions do not duplicate stock. Removed lines remain in Approval details.

5. **Sharing and stock context.** The request form shows the live stock position
   of each selected item (stock now, pack breakdown for bagged materials, reorder
   level, and the level once the request is received). The review page shows the same
   and projects the post-receipt level as quantities change. **Share on WhatsApp**
   sends the request as text plus a PNG snapshot (stock states, totals and a QR code)
   through the device share sheet where the browser can attach files; otherwise the
   text opens in WhatsApp and the image can be saved from **More ways to share**.
   **View snapshot** opens the full-size image in the browser's image viewer. The
   text and image carry a link worded "Open INPROFIC to approve this purchase request
   (sign-in required)". No WhatsApp template or API is involved, nothing is public,
   and the snapshot endpoint needs a signed-in member with Procurement access.

Requests with payment history and approved/received documents cannot be edited
or deleted. Historical received orders remain received, without invented approval
metadata. Existing unreceived drafts/ordered records require approval. Procurement
access and receipt retain their module permissions; approval requires business
administrator access. Approval is separate from the ordinary edit form.

## Integrity corrections

- Paid expense corrections reverse the previous cash posting through a linked
  compensating entry before posting the corrected payment. Payments cannot exceed
  the remaining balance; Finance shows remaining expense obligations.
- Recorded Sales, received purchases and expenses with payment history cannot be
  deleted. Use the appropriate auditable reversal/return workflow.
- Raw-material stock helpers lock fresh records before calculating balances.
  Sales lock products and validate combined duplicate-line quantities. Production
  approvals/completions lock their records and recheck state before mutation.
- Sale lines freeze their batch yield so later product changes do not rewrite
  quantities or SQL revenue/cost reports. **Legacy rows use the yield available
  at migration**, marked `legacy_current_yield`; missing historical yield cannot
  be reconstructed. New rows capture the actual yield at sale time.
- Books and financial packs value all stock products, without the former 1,000
  product ceiling. Invalid costs are flagged rather than silently declared tied.
  Derived Books still require an explicit build/rebuild.

## Backup v2

JSON exports now contain a versioned envelope and expanded tenant operational
records, including tax, Books, payroll, Commerce and WhatsApp. Restore also accepts
legacy JSON lists. IDs in ledger opening positions/cut-over history, references,
assembly components, payment-route snapshots and production reports are remapped.
Manual journals are preserved; system Books are rebuilt when journals exist.

Restores disable external integrations/queued outbound automation and clear known
plaintext credentials. Reconfigure and reconnect providers after restoring.
Encrypted credentials require the original encryption key. File uploads are not
embedded. Platform billing, identities, user preferences and share tokens are not
portable tenant operations. Existing global unique identifiers are not silently
rewritten to permit live-store cloning.

## Other connected fixes

Founder marketing changes invalidate cached pages. Anonymous WhatsApp webhook
requests reach signature verification, and duplicate events use isolated savepoints.
Checkout handoff requires `SITE_URL`. Provider-free WhatsApp regression coverage
checks verification, deduplication, tenant routing, send windows and bounded retries.
Public checkout uses proof-backed Transfer; Cash remains a staff POS method.

## UI

Embedded forms use one outer dialog surface without redundant page panel borders
or shadows. Dialogs have short opening/closing motion and a subtle 2px backdrop
blur, with reduced-motion support and no “Open full page” action. Interior field
sections, dirty-form confirmation, validation and theme-aware text are preserved.
Dashboard search/ticker styling and onboarding scripts are cached static assets.

## Follow-up completion (31 October 2026)

- **Sale-line yield snapshot everywhere.** Finance open items (customer
  receivables), the Audit workspace and the dashboard ledger still computed
  units from the product's *current* `units_per_batch`. They now use the same
  `_sale_expressions` helper as Books, tax, reports and the Daily Brief, so a
  later yield change cannot move a receivable or dashboard total.
- **Expense amount increase.** Raising the amount of an expense already settled
  through payments no longer errors with a misleading "mark paid" message; the
  balance reopens and the status is derived from the payments. Explicitly
  marking an unpaid expense paid without matching payments is still refused.
- **Operational P&L vs Books.** Both pages now say that operational P&L counts
  fully paid sales (cash basis) while Books recognise sales when recorded
  (accrual basis), so their revenue can differ while sales are unpaid.
- **Tests.** Two stale assertions were updated (approved PO status before
  receipt; search script now read from its static file) and two regression
  tests were added for the yield snapshot and the expense amount increase.

- **Brief dates and weekly summary.** The Daily brief page accepts a chosen date
  and has a Weekly view with a week's profit and loss (migration
  `core/0016_daily_brief_weekly_summary` adds two nullable/default preference
  fields). Procurement approval no longer says a received request "predates the
  approval workflow"; it reads "This request has already been received."

- **Quick facts and assistant settings.** 22 deterministic quick facts (sales,
  profit, expenses, cash, receivables/ageing, payables, VAT, stock, recipe check,
  approvals) answer typed questions without an AI model via an explicit keyword
  matcher. Controls are layered: environment switch, Founder Console (main switch,
  quick facts, AI answers, per-fact, pilot or open access), then each business's own
  Assistant settings. Migration `assistant/0002`.
- **Staff assistant (read-only pilot).** New `assistant` app and `/assistant/`
  panel; see `STAFF_ASSISTANT.md`. Migrations `assistant/0001_initial` and
  `core/0017_business_assistant_enabled` (pilot flag, default off). Also fixed
  the brief middleware so view-only members can save the weekly preference and
  email themselves a brief.

## Deployment and verification

Run `python manage.py migrate` before serving the new SaleItem/procurement fields the brief weekly-summary preference fields and the assistant tables.
Rebuilt UI bundles are included. These changes do not deploy or alter production
data. Test evidence is recorded in `SYSTEM_REVIEW_TEST_FAILURES.md` (latest run: 868 passed, 0 failed).
Provider sandboxes and live cross-worker PostgreSQL contention
require deployment-environment verification; local checks use isolated SQLite.
