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
