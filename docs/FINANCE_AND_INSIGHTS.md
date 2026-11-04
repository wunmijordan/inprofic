# Finance, Insight and Catch-up Tools

Added alongside the ledger cut-over (see `LEDGER_CUTOVER.md`).

## Recipe vs Actual  (`/orders/variance/`, Executive dashboard > Production)

Compares, per ingredient, what the recipe planned for approved orders
(`OrderMaterialUsage.planned_quantity`) with what was actually released, adds stock
written off through adjustments (shrinkage no recipe explains), and compares
planned, produced and wasted units on batches. Differences over 5% are flagged.
CSV export included. Read-only; it changes no stock or cost.

## Bank reconciliation  (`/finance/reconcile/`)

Load a statement CSV (one signed *Amount* column, or *Debit* and *Credit*; dates
YYYY-MM-DD or DD/MM/YYYY), auto-match by exact amount within 3 days, match the rest
by hand, and prove the balance with the adjusted-balance method:
`statement closing + book entries not on the statement = book balance + statement
lines not in the books`. A statement can be marked reconciled only when the
difference is zero, and is then locked. Reconciling never changes cash records.
It works best when consecutive statements are reconciled in turn.

## Financial pack  (`/finance/pack/`)

Profit and loss, a statement of financial position (cash, receivables, stock at
cost, payables, net assets), cash accounts and receivables/payables ageing, with
a print / save-as-PDF button. **It is derived from operational records; it is not
a double-entry general ledger and has no tax computation**, and says so on its face.

Business admins can create **share links** (`/finance/pack/shares/`): read-only,
expiring (1 to 30 days), revocable, usable without logging in, `noindex`,
uncached. Only a hash of the token is stored, and the link is shown once. Creating
and revoking are audited. Unknown, expired and revoked links are indistinguishable.

## Stock counts for catch-up  (`/ledger/` > Count your stock)

A phone-friendly count screen, a downloadable count sheet and a CSV upload confirm
the provisional openings. Only rows with a count are applied, and a count that
equals the provisional figure still marks it as counted.

## Daily brief and weekly summary  (`/brief/`)

**Daily and weekly business coverage.** Both reports include complete, business-scoped
material and product stock lists (registered units, reorder thresholds, out/low/warning
status), recorded sales and sales counts, average sale, channel/payment/product/customer
breakdowns, canonical Finance P&L and every expense category, cash movements by category,
current active account balances, receivables/payables and their full invoice detail,
current purchasing queues and period receipts. Production businesses additionally show
period gross/planned/saleable output and wastage per product/unit, costs, approval queues,
current recipe-review counts, Market Stock lots and expiry, allocations to customers or
Physical Store, and actual returns. Purchased-stock businesses keep their warehouse/shop
positions and actual returns without an invented production pool. Commerce and delivery
status counts appear when permitted. Every section follows the sender's module access;
stock and balances are explicitly current, not historical closes. Settlement balances
span Live and Backlog through the shared Finance helper; period flows follow the active scope.

**Periods and comparisons.** Daily defaults to yesterday, with older dates and today selectable.
Weekly defaults to the last Monday–Sunday week. An in-progress week uses elapsed days and
compares against the same days of the preceding week. Weekly text retains revenue/profit
changes, sales count, best sales day, margins and unpaid-sales/expense notes. P&L revenue
recognises fully paid sales and expenses when incurred, matching Finance. Books recognition
can differ. Quantities in different registered units never combine.

**Image versus full text.** The shared paper-style PNG is a deliberately bounded executive
snapshot: at most 8 headline rows, net profit/loss when permitted, and an authenticated
Executive dashboard QR/link. Its highlights cover sales versus paid revenue, gross profit,
operating costs, current cash, receivables/payables, stock risks (with named quantities),
and operational queues. Full on-screen, copied, manual WhatsApp and native image-sharing
text includes every report section without the image's row limit or Finance screen detail
caps. Native file sharing receives the same full text as Copy text. The unsupported-file-sharing
fallback saves the image and copies the complete accompanying text when clipboard access
is available. The application does not shorten text to fit a caption.

**Delivery.** Members can opt in to a morning daily email (after 7am) and/or a
weekly summary email (after 7am, for the week just ended, starting from the next
Monday), via the `send_daily_brief` scheduled command. Automatic WhatsApp
delivery is for the daily brief only and uses a Meta-approved utility template
configured by a business admin on the same page; the weekly summary is shared
manually on WhatsApp or by email. Manual text sharing needs no Meta template. Morning and weekly emails carry the full executive report; automatic WhatsApp retains its approved compact eight-parameter template rather than silently changing the template contract.
See `BOOKS_TAX_AND_WHATSAPP.md`.

## Sample recipes  (`/inventory/sample-setup/`)

Offered to production-style businesses (bakery, restaurant, general) that have no
recipes: a zero-stock example, marked "(Sample)", showing a base ingredient and
ratios such as "1 kg per 20 kg Flour".

## Books and tax

Finance now links Books, Tax & VAT and E-invoices through a shared tools bar.
Books are rebuilt from operational records, VAT depends on business/product
settings, and e-invoice clearance needs a configured provider or manually
recorded clearance. See `BOOKS_TAX_AND_WHATSAPP.md` for setup and limitations.
Importing historical backlog transactions from CSV remains outside these tools.

## Report contents and post-distribution settlement

Daily Brief and Weekly Summary each have a per-member, per-business selection of
permitted sections. Missing preferences include all permitted data; an explicitly
empty selection shares no report sections. On-screen text, copied text, native sharing,
image highlights and scheduled email follow the selection. The compact approved
WhatsApp template keeps its existing parameter contract, with omitted figures marked
as not included. Supplier and expense payables include the recorded payee/vendor.

Market Stock distribution sharing includes a Finance-permission-gated settlement
review. Production profiles include only customer invoices released from the Market
Stock pool; purchased-stock profiles use distribution invoices. Reversed orders are
excluded. Original invoice selling values (including discounts and commercial quantity
snapshots) price returns proportionally. The report shows gross distributed value,
returned selling value, net retained value before costs, collections, outstanding
invoice value, estimated balance after returns and overpayment/refund review. Later
payments and returns are included for the selected invoices, across ledger segments.

This is a read-only analysis. Inventory returns currently do not credit an invoice or
refund cash. Outstanding invoices remain unchanged until a commercial adjustment is
recorded; the after-return balance is analytical. Fully paid invoices may have been
collected at creation without a separate customer-payment row. Collections therefore
use paid invoice state or recorded payments without adding them twice. Physical Store
transfers are not sales, and customer settlement does not prove end-customer sell-through.

## Vendor requests and split customer collections

A multi-vendor request is an approval envelope over vendor-specific PurchaseOrders.
The procurement list, editor, approval and sharing show the whole request. Each vendor
retains its own total, receipt status, advances, cost snapshots and SupplierPayments.
The default vendor fills only unnamed item rows. For a partial initial payment, the
request identifies the vendor paid; an advance cannot exceed that vendor's total.
Paid requests post each vendor's full amount to the selected account separately.
A request with any posted vendor payment or approved/received vendor cannot be edited
or deleted as an unpaid draft. Approval checks advances against each vendor's revised
total; it cannot use another vendor's unspent balance to cover a reduction.

Vendor payment entry can record an advance before receipt, or settle a received
invoice, without moving stock. Receipt remains an independent, idempotent vendor action.
Backups include the request envelope and preserve its vendor relationships.

Customer Payment opens from Finance or a distribution invoice row. Enter any positive
amounts across Cash, Card and Transfer; unused methods stay at zero. Each part has its
own active tenant-owned receiving account and optional reference. The invoice is
locked while checking the combined amount against its lifetime outstanding balance.
The entire receipt commits together or records nothing. Partial collections leave the
sale Partially Paid and its remaining balance in Finance; later receipts can use any
supported methods and clear the receivable when fully settled. The original sale's
payment state and linked customer-order settlement update without moving stock again.
Cash ledger entries preserve the individual methods/accounts for bank reconciliation.

Distribution image sharing follows the chosen distribution date, with a compact
made/received → customer allocations → current stock table and financial cards. The
image is limited to eight products and sixteen destination lines with omission counts;
the detailed accompanying text is unlimited. Made quantities exclude returned goods.
Purchased-stock profiles show received product quantities instead of invented production.
Financial cards concern the selected invoices and include later payments/returns; they
are not a daily cash-flow statement or a profit calculation. Current stock is explicitly
labelled, including when the selected distribution date is in the past.
