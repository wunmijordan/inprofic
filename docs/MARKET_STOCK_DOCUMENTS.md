# Market Stock invoices and outflow documents

Open **Stock Distribution → Where stock went** and click a customer or another
destination. Customer names open Sales activity. Physical Store sales, Online
guests, Physical Store transfers and staff/gifting recipients open their own
activity view. Choose **From** and **To** before creating the document.

**Invoice this period** uses every selected sale, including sales beyond the
visible activity page. It uses saved selling quantities, prices and discounts,
with receipts and return credits through the selected end date. Payments and
returns recorded after that date do not change the earlier period's payment
status. The activity page uses the same cutoff.

**Include earlier outstanding balances** is unchecked by default. When checked,
the invoice brings forward the unpaid balance entering the period on earlier
sales for that customer or destination, then applies payments and return credits
recorded during the selected period. Fully settled earlier invoices are omitted.
Earlier balances are identified separately from sales in the selected period.

Invoices reuse the storefront receipt document, including its business logo,
contact details, typography, product layout and print styling. Use **Download /
print invoice** to print or save a PDF. Physical Store transfers use **Stock
transfer**, and staff/gifting withdrawals use **Stock issue**, with payment shown
as **Not applicable**. These documents do not create a sale, cash receipt or tax
clearance record. Documents retain the active business and ledger scope and are
available only to users permitted to view the corresponding records.

## Corrections

**Edit** on a standalone Market Stock customer, Online or direct Physical Store
sale uses the existing release-correction workflow. Inventory, Sales and Finance
edit access are required. The correction requires a reason and preserves the
release date and actual receipts. Existing product prices remain saved unless
the customer changes; new products use the destination channel's configured
price. Returns and submitted e-invoices protect the original sale lines.

**Edit issue** on a Market Stock staff/gifting outflow requires Inventory edit
access and a correction reason. Product, quantity, recipient, purpose and note
can be corrected; the issue date remains fixed. The original adjustment remains
as reversed history, compensating movements restore its batch allocation, and a
replacement issue records the corrected withdrawal and cost. No sale or cash
receipt is created. Insufficient stock, changed records and written-off source
batches block the correction without changing stock. Reversed issues are omitted
from active issue totals, so Finance records the inventory cost once.
