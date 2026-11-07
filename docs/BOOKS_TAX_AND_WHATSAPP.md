# Books, Tax (VAT and e-invoicing) and WhatsApp brief delivery

## Where to find them

Daily brief appears below Dashboard in both desktop and mobile navigation for users
with Dashboard view access. Each member can save their own email/WhatsApp choices
without permission to edit operational records. The automatic WhatsApp section
shows connection/template readiness; business admins configure the approved template.
Manual Copy text / Send to WhatsApp remains available without a Meta template.

Open Finance for the shared Finance tools bar: Finance overview, Books, Tax & VAT,
E-invoices, Bank reconciliation and Financial pack. Books and Tax & VAT also appear
under Sales & Finance in both menus. These surfaces require Finance access under
the business's plan. Finance viewers can read; edit access is needed for write
actions, with additional business-admin checks on tax settings and manual journals.

## Double-entry books  (`/finance/books/`)

A chart of accounts, journal, trial balance, account registers, income statement and
balance sheet, **derived from your records** by posting rules and rebuilt on demand
(`Build books` / `Rebuild books`). Every entry balances by construction and the database
refuses a line that is both debit and credit. Manual journals (opening balances,
adjustments) are kept across rebuilds and can only be reversed, never edited.

* Documents post the accrual side (receivable, payable, accrued expense, inventory, revenue,
  cost of goods sold); each cash movement then settles one of them, chosen from its
  reference and category. Unrecognised cash goes to **Suspense** so cash always ties.
* The books read both ledger periods (Live and Backlog).
* **Checked against your records** compares cash (must tie exactly), receivables, payables
  and inventory with the operational figures and explains differences.

Known simplifications: stock held before the books began needs an opening-balance manual
journal; production conversion costs beyond materials are not capitalised; sales are
recognised when recorded. Manual journals and chart edits are business-admin only.

## VAT  (`/finance/tax/`)

Settings (rate, registration date, threshold, due day, TIN) are editable, never hard-coded
law; confirm figures with the tax authority. Prices are treated as **VAT-inclusive**; VAT is
extracted (`gross x rate / (1 + rate)`) for standard-rated products. Each product is standard,
zero-rated or exempt (zero-rated sales allow input-VAT recovery; exempt do not). The VAT return
shows output VAT, recoverable input VAT from the **input VAT register**, and the net position
with a due date; the VAT register downloads as CSV. A registration monitor compares 12-month
sales with the threshold. Output VAT also posts to the books (VAT payable) and input VAT to VAT
receivable.

## E-invoicing

Storefront sales are eligible once Commerce creates their Sale record. Pending
checkouts/pre-orders are not themselves invoice sources, and checkout does not
automatically prepare or clear an e-invoice.

Structured, UBL-style invoice data per sale with sequential numbering, buyer TIN, VAT by line
and a status trail (ready, submitted, cleared, rejected, failed). Clear it by **posting to your
accredited access-point provider over HTTPS** (https only, internal addresses refused, key stored
encrypted and never shown back) or record the IRN, stamp and QR text by hand after submitting in a
portal. Map the payload to your provider's exact schema before going live; submission against a
real provider has not been tested.

## WhatsApp brief by template

Register `daily_brief.TEMPLATE_BODY` as a **Utility** template in WhatsApp Manager, then enter its
name on `/brief/` (business admin). Members add their number and consent, then switch delivery on.
One attempt a day; on failure the email is sent instead. "Send me a test now" is capped at 3 a day.
Text-only copy/tap-to-send and the morning email still work as before. Requires the
`WHATSAPP_TOKEN_ENCRYPTION_KEY` server secret; Meta charges for business-started messages.

### Purchase request and distribution summaries

Purchase requests group items by optional vendor name. Staff can select a saved
vendor, type an ad hoc name, or leave it blank. Each group has its own payment
status, method, account and optional advance. The combined approval, preview,
WhatsApp text and image retain these group boundaries; receipt and settlement
remain attributable to each vendor order. Unapproved requests remain editable
and deletable, with confirmation warnings for paid or partially paid requests.
Recorded vendor/payment details are preserved while items can change; totals
cannot fall below recorded payments. Deletion retains payments in Finance as
unallocated supplier payments and never fabricates a refund. Before/after request
audits and payment-link audits preserve the reconciliation trail. Settle later
balances and refunds through Finance.

Daily and weekly report text puts colored stock status markers before each
material/product and mentions a reorder threshold only when one is configured.
Ledger scope labels appear on shared summaries only for businesses that have
configured ledger cut-over. Distribution summaries show expected selling value
as distributed value less recorded returns, and expected balance as that value
less collections. Recorded returns now credit the original customer invoice in
customer balances, payment validation and Finance. The original invoice lines
and actual cash receipts remain intact; any excess payment stays available for
refund/credit review. Reselling returned stock creates a separate sale for its
new customer or the Physical Store. Damaged returns appear as damaged
distribution goods cost in Profit & Loss at their recorded stock cost. Rebuild
Books to include dated return-credit and stock/damage journals; this does not
automatically post a cash refund.

Customer region and area labels on distribution pages, images and shared text
come from the current business customer directory. Historical sale-name snapshots
remain intact. Labels are omitted when unset; a customer link from another
business is never used to resolve labels.

Procurement receiving uses one request-row button to open the shared vendor
receipt modal. Each vendor retains its own receipt date and stock transaction.
Distribution page and sharing stock figures use selected-date opening and closing
balances. The snapshot shows opening instead of new production; detailed text also
shows new receipts, returns, transfers and write-offs. Closing includes blocked
batches until their write-off is recorded. Later movements do not change an
earlier closing balance; invoice settlement still includes later payments.

## Operational summaries and direct Market Stock sales

Daily and weekly reports group production by product, label production costs,
show material stock as purchase packages plus usage-unit remainder, and include
distribution detail within Sales activity. Purchasing includes miscellaneous
expenses for the period. Empty and zero-value financial fields are omitted;
zero stock remains visible when it indicates an out-of-stock item.

Market Stock supports paid Physical Store sales without a shelf transfer. One
sale records the applied Physical Store price, frozen lot costs, payment account,
stock movement and audit trail. The distribution summary separates these sales
from customer releases and Physical Store transfers. Write-offs and reversals
are named explicitly; residual stock out is displayed as a positive quantity
under its own label, rather than an unexplained negative change.

Customer/product agreements override channel prices, followed by the product
default where that selling flow permits a default. Market Stock distribution
requires an explicit Distribution or customer price. Release forms preview the
applied price; product search lists current agreements and historical applied
prices. Existing invoices and profitability retain saved selling prices after
an agreement changes unless an authorized editor explicitly previews and confirms a period correction. The customer pricing review supports all existing distributions or a selected period. Actual recorded receipts remain fixed by default. Mistaken receipt amounts can be corrected only through a separate reviewed choice: original cash entries are retained and offset, corrected receipts retain their accounts/methods, and the previous/new payment and invoice amounts are audited atomically. Partial receipts require the actual amount; ambiguous cash links and submitted e-invoices are blocked. Stock quantities and frozen unit costs stay unchanged. Rebuild derived Books for corrected records. Finance still recognises paid-sales revenue only once an
invoice is fully settled, consistent with its existing Profit & Loss definition.

Procurement snapshots and full sharing text include miscellaneous expenses for
the request date when Expenses or Finance view access permits. The expense subtotal and purchase value combine into the shared grand total; purchase approval and the vendor payable retain their purchase-only value. The Purchases & expenses workspace also shares a combined selected-period snapshot/text and exports. Actual money paid out uses dated cash records, including later settlements and compensating entries, rather than adding invoice statuses and payment rows together. Unpaid entries contribute to recorded value but not to cash-out.
