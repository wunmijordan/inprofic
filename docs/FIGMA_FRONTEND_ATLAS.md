# INPROFIC Figma frontend atlas

[Open the editable Figma file](https://www.figma.com/design/MYzvvP3xgWwubeWRFYrRsl)

## Status

Prepared 12 workflow groups, with 246 screen/state configurations at 1440px and 390px. Figma confirmed 182 screen/state imports across 18 completed galleries before its Starter MCP limit stopped further design calls. Six galleries were unfinished at the last verified status. This is an incomplete refinement handoff, not a claim that every frontend flow is finished.

The last verified organization contains 48 desktop screens and 74 phone screens in named module sections. Other completed captures are still in import staging on the phone-flow canvas. Some backend conversions may finish after this report; inspect the file before retrying.

All captures use an isolated, disposable SQLite UI fixture. Commerce, Delivery and Audit were enabled only in that fixture for supplemental captures. No live business database, tenant settings, stock or payments were changed. Temporary edits to `templates/base.html` and `scripts/ui/urls.py` were restored.

## Canvas structure

- `00 Guide & shared UI`: editable guide, seven main components, text styles, source theme variables, compact phone dialog, long scrolling phone dialog, and consistent-table example.
- `01 Desktop flows`: named module sections; page ID `22:2`.
- `02 Phone flows`: named module sections and remaining imported galleries; page ID `0:1`.

The file stays within the Starter plan’s three-page limit. Captured page content uses editable native layers. Logos and other original raster content remain images. The reusable component examples are separate refinement proposals; the captured baseline has not been fully converted into instances of those components.

## Confirmed design direction

- Preserve Sora body/controls, Fraunces page headings, IBM Plex Mono quantities, neutral text and surfaces, tenant-colored action fills, and Inventory stock-state colors.
- Forms launched from table lists are proposed as dialogs. On phones, long forms use full-height dialogs; short forms use compact dialogs, as explicitly selected by the user.
- Keep visible validation, save/cancel actions, a clear title, Escape/focus/dirty-form behavior, and the underlying Django form/formset/CSRF/authorization boundaries.
- Dense tables retain their own horizontal scrolling and pinned first column; page content and toolbars adapt to narrow screens.
- Figma is a visual design handoff. No application-wide modal or responsive behavior has been implemented by this task.

## Unfinished work

1. Inspect ongoing conversions and complete the six galleries below. Three capture IDs expired after failed uploads and need fresh IDs once MCP access is available.
2. Organize all remaining imports into desktop/phone module sections; preserve detached text layers when moving imported galleries.
3. Componentize repeated baseline controls and connect table-to-form prototype transitions. These captures are visual states, not a complete clickable prototype.
4. Finish dark-theme examples and final visual review. The shared variable collection currently has only its Light mode. The guide’s sentence referring to Light and Dark modes is premature and needs correction. A separate single-mode Dark token collection was planned because of Starter limits, but its creation was rejected by the MCP quota.
5. Verify final compact-dialog sizing after the applied width/height corrections, and review the new full-height dialog. The first compact-dialog screenshot exposed sizing issues; the structural corrections succeeded, but the final screenshot request failed in transport.
6. Extend record/role-dependent coverage, including actual paid checkout/status/receipt flows, rider assignments, Founder-only surfaces, richer populated records, and additional dynamic review states. Current captures do not exhaust every route, tab, empty/error state or provider flow.

Accessibility review criteria include named controls, visible focus, inline errors, modal focus/scroll behavior, and safe-area handling. Static Figma layers cannot verify keyboard, submission, authorization or responsive application behavior. Reference: [Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md).

## Import batches

| Group | States per device | Desktop | Phone |
| --- | ---: | --- | --- |
| 01 Dashboard and navigation | 7 | completed (original node 4:2; may now be organized) | completed (original node 24:2; may now be organized) |
| 02 Inventory | 10 | processing | completed (original node 13:2; may now be organized) |
| 03 Market stock | 8 | completed (original node 28:2; may now be organized) | Expired; fresh capture ID required |
| 04 Procurement and expenses | 10 | completed (original node 6:2; may now be organized) | completed (original node 9:2; may now be organized) |
| 05 Production | 11 | completed (original node 29:2; may now be organized) | completed (original node 7:2; may now be organized) |
| 06 Sales and customers | 7 | completed (original node 12:2; may now be organized) | Expired; fresh capture ID required |
| 07 Finance and reporting | 12 | Expired; fresh capture ID required | completed (original node 30:2; may now be organized) |
| 08 Books and tax | 10 | completed (original node 14:2; may now be organized) | completed (original node 10:2; may now be organized) |
| 09 Commerce and delivery | 14 | completed (original node 17:2; may now be organized) | completed (original node 21:2; may now be organized) |
| 10 Administration and public | 12 | processing | completed (original node 19:2; may now be organized) |
| 11 Enabled commerce delivery and audit | 15 | processing | completed (original node 37:2; may now be organized) |
| 12 Storefront and interaction states | 7 | completed (original node 36:2; may now be organized) | completed (original node 39:2; may now be organized) |

## Route/state coverage

Rows describe prepared configurations. Import completion is given in the batch table above. “Modal proposal” indicates the intended table-to-form presentation; current application routing is unchanged. Restricted/redirect/export states are explicitly identified rather than treated as usable forms.

| Group | Screen/state | Source route | Presentation | Observation |
| --- | --- | --- | --- | --- |
| 1 | Basic dashboard | `/dashboard/?view=simple` | Current UI | Rendered fixture UI |
| 1 | Executive dashboard | `/dashboard/complete/` | Current UI | Rendered fixture UI |
| 1 | Daily brief | `/brief/` | Current UI | Rendered fixture UI |
| 1 | Business settings | `/business/settings/` | Current UI | Rendered fixture UI |
| 1 | Ledger workspace | `/ledger/` | Current UI | Rendered fixture UI |
| 1 | Assistant | `/assistant/` | Current UI | Redirect destination; not an independent captured form |
| 1 | Assistant settings | `/assistant/settings/` | Current UI | Rendered fixture UI |
| 2 | Raw material inventory | `/inventory/#raw` | Current UI | Rendered fixture UI |
| 2 | Finished goods inventory | `/inventory/#finished` | Current UI | Rendered fixture UI |
| 2 | Product categories | `/inventory/product-categories/` | Current UI | Rendered fixture UI |
| 2 | Add raw material | `/inventory/raw-material/add/` | Modal proposal | Rendered fixture UI |
| 2 | Edit raw material | `/inventory/raw-material/1/edit/` | Modal proposal | Rendered fixture UI |
| 2 | Add product | `/inventory/product/add/` | Modal proposal | Rendered fixture UI |
| 2 | Edit product and recipe | `/inventory/product/1/edit/` | Modal proposal | Rendered fixture UI |
| 2 | Product detail | `/inventory/product/1/detail/` | Current UI | Rendered fixture UI |
| 2 | Stock movement history | `/inventory/stock-history/raw/1/` | Current UI | Export/data endpoint or blank frame; actual UI state still required |
| 2 | Inventory alert settings | `/inventory/alert-settings/` | Current UI | Rendered fixture UI |
| 3 | Market stock and distributions | `/inventory/market-stock/` | Current UI | Rendered fixture UI |
| 3 | Release to customer | `/inventory/market-stock/release/` | Modal proposal | Rendered fixture UI |
| 3 | Online sale | `/inventory/market-stock/sell-online/` | Modal proposal | Rendered fixture UI |
| 3 | Physical store sale | `/inventory/market-stock/sell/` | Modal proposal | Rendered fixture UI |
| 3 | Transfer to physical store | `/inventory/market-stock/transfer/` | Modal proposal | Rendered fixture UI |
| 3 | Customer return | `/inventory/market-stock/return/` | Modal proposal | Rendered fixture UI |
| 3 | Staff and gifting issue | `/inventory/product-use/market/` | Modal proposal | Rendered fixture UI |
| 3 | Supporting supply dispense | `/inventory/operational-supply/dispense/` | Modal proposal | Rendered fixture UI |
| 4 | Purchasing workspace | `/procurement/` | Current UI | Rendered fixture UI |
| 4 | Purchase request | `/procurement/add/` | Modal proposal | Rendered fixture UI |
| 4 | Direct purchase | `/procurement/direct/add/` | Modal proposal | Rendered fixture UI |
| 4 | Add supplier | `/procurement/suppliers/add/` | Modal proposal | Rendered fixture UI |
| 4 | Approve purchase | `/procurement/1/approve/` | Modal proposal | Rendered fixture UI |
| 4 | Receive purchase | `/procurement/1/receive/` | Modal proposal | Redirect destination; not an independent captured form |
| 4 | Expenses workspace | `/expenses/` | Current UI | Redirect destination; not an independent captured form |
| 4 | Expense form | `/expenses/add/` | Modal proposal | Rendered fixture UI |
| 4 | Expense request | `/expenses/requests/add/` | Modal proposal | Rendered fixture UI |
| 4 | Expense payment | `/expenses/payments/add/` | Modal proposal | Rendered fixture UI |
| 5 | Production orders | `/orders/` | Current UI | Rendered fixture UI |
| 5 | Production order form | `/orders/add/` | Modal proposal | Rendered fixture UI |
| 5 | Production order detail | `/orders/1/` | Current UI | Rendered fixture UI |
| 5 | Complete production | `/orders/1/complete/` | Modal proposal | Rendered fixture UI |
| 5 | Shared production runs | `/runs/` | Current UI | Rendered fixture UI |
| 5 | Shared run form | `/runs/add/` | Modal proposal | Rendered fixture UI |
| 5 | Production batches | `/batches/` | Current UI | Rendered fixture UI |
| 5 | Production variance | `/orders/variance/` | Current UI | Rendered fixture UI |
| 5 | Production settings | `/settings/` | Current UI | Rendered fixture UI |
| 5 | Production proposals | `/orders/proposals/` | Current UI | Rendered fixture UI |
| 5 | Proposal form | `/orders/proposals/add/` | Modal proposal | Rendered fixture UI |
| 6 | Sales list | `/sales/` | Current UI | Rendered fixture UI |
| 6 | Record a sale | `/sales/add/` | Modal proposal | Rendered fixture UI |
| 6 | Customer list | `/sales/customers/` | Current UI | Rendered fixture UI |
| 6 | Add customer | `/sales/customers/add/` | Modal proposal | Rendered fixture UI |
| 6 | Edit customer | `/sales/customers/1/edit/` | Modal proposal | Rendered fixture UI |
| 6 | Customer activity and prices | `/sales/customers/1/activity/` | Current UI | Rendered fixture UI |
| 6 | Sale invoice | `/sales/1/invoice/` | Current UI | Export/data endpoint or blank frame; actual UI state still required |
| 7 | Finance dashboard | `/finance/` | Current UI | Rendered fixture UI |
| 7 | Cash account form | `/finance/accounts/add/` | Modal proposal | Rendered fixture UI |
| 7 | Supplier payment | `/finance/supplier-payment/add/` | Modal proposal | Rendered fixture UI |
| 7 | Customer payment | `/finance/customer-payment/add/` | Modal proposal | Rendered fixture UI |
| 7 | Stock adjustment | `/finance/stock-adjustment/add/` | Modal proposal | Rendered fixture UI |
| 7 | Profit and loss | `/finance/profit-loss/` | Current UI | Rendered fixture UI |
| 7 | Profit comparison | `/finance/profit-loss/compare/` | Current UI | Rendered fixture UI |
| 7 | Product profitability | `/finance/products/` | Current UI | Rendered fixture UI |
| 7 | Reconciliation | `/finance/reconcile/` | Current UI | Rendered fixture UI |
| 7 | Reports | `/reports/` | Current UI | Rendered fixture UI |
| 7 | Audit workspace | `/audit/` | Current UI | Restricted state; see enabled supplemental group |
| 7 | Finance pack | `/finance/pack/` | Current UI | Rendered fixture UI |
| 8 | Books overview | `/finance/books/` | Current UI | Rendered fixture UI |
| 8 | Chart of accounts | `/finance/books/accounts/` | Current UI | Rendered fixture UI |
| 8 | Journal | `/finance/books/journal/` | Current UI | Rendered fixture UI |
| 8 | Journal entry | `/finance/books/journal/new/` | Modal proposal | Rendered fixture UI |
| 8 | Financial statements | `/finance/books/statements/` | Current UI | Rendered fixture UI |
| 8 | Tax overview | `/finance/tax/` | Current UI | Rendered fixture UI |
| 8 | VAT return | `/finance/tax/vat/` | Current UI | Rendered fixture UI |
| 8 | Input VAT | `/finance/tax/input-vat/` | Current UI | Rendered fixture UI |
| 8 | Product VAT | `/finance/tax/products/` | Current UI | Rendered fixture UI |
| 8 | E-invoices | `/finance/tax/einvoices/` | Current UI | Rendered fixture UI |
| 9 | Commerce overview | `/commerce/` | Current UI | Restricted state; see enabled supplemental group |
| 9 | Commerce settings | `/commerce/settings/` | Current UI | Restricted state; see enabled supplemental group |
| 9 | Commerce payments | `/finance/commerce-payments/` | Current UI | Rendered fixture UI |
| 9 | Payment settings | `/commerce/payment-settings/` | Current UI | Restricted state; see enabled supplemental group |
| 9 | Integration form | `/commerce/integrations/add/` | Modal proposal | Restricted state; see enabled supplemental group |
| 9 | Storefront POS | `/commerce/storefront-pos/` | Current UI | Restricted state; see enabled supplemental group |
| 9 | Delivery overview | `/delivery/` | Current UI | Restricted state; see enabled supplemental group |
| 9 | Delivery settings | `/delivery/settings/` | Current UI | Restricted state; see enabled supplemental group |
| 9 | Delivery origin form | `/delivery/origins/add/` | Modal proposal | Restricted state; see enabled supplemental group |
| 9 | Delivery area form | `/delivery/areas/add/` | Modal proposal | Restricted state; see enabled supplemental group |
| 9 | Provider form | `/delivery/providers/add/` | Modal proposal | Restricted state; see enabled supplemental group |
| 9 | Driver form | `/delivery/drivers/add/` | Modal proposal | Restricted state; see enabled supplemental group |
| 9 | WhatsApp settings | `/commerce/whatsapp/` | Current UI | Restricted state; see enabled supplemental group |
| 9 | WhatsApp inbox | `/commerce/whatsapp/inbox/` | Current UI | Restricted state; see enabled supplemental group |
| 10 | Users list | `/users/` | Current UI | Rendered fixture UI |
| 10 | User form | `/users/add/` | Modal proposal | Rendered fixture UI |
| 10 | Roles list | `/users/roles/` | Current UI | Rendered fixture UI |
| 10 | Role form | `/users/roles/add/` | Modal proposal | Rendered fixture UI |
| 10 | Payroll workspace | `/users/payroll/` | Current UI | Rendered fixture UI |
| 10 | Subscription plans | `/users/plans/` | Current UI | Rendered fixture UI |
| 10 | Additional service | `/users/plans/services/add/` | Modal proposal | Redirect destination; not an independent captured form |
| 10 | Marketing homepage | `/?view=marketing` | Current UI | Rendered fixture UI |
| 10 | Privacy policy | `/privacy-policy/` | Current UI | Rendered fixture UI |
| 10 | Hosted storefront | `/shop/ui-fixture/` | Current UI | Unavailable while Commerce disabled; see enabled storefront group |
| 10 | Customer registration | `/shop/ui-fixture/account/register/` | Current UI | Unavailable while Commerce disabled; see enabled storefront group |
| 10 | Customer login | `/shop/ui-fixture/account/login/` | Current UI | Unavailable while Commerce disabled; see enabled storefront group |
| 11 | Commerce overview | `/commerce/` | Current UI | Rendered fixture UI |
| 11 | Commerce settings | `/commerce/settings/` | Current UI | Rendered fixture UI |
| 11 | Commerce payments | `/finance/commerce-payments/` | Current UI | Rendered fixture UI |
| 11 | Payment settings | `/commerce/payment-settings/` | Current UI | Rendered fixture UI |
| 11 | Integration form | `/commerce/integrations/add/` | Modal proposal | Rendered fixture UI |
| 11 | Storefront POS | `/commerce/storefront-pos/` | Current UI | Rendered fixture UI |
| 11 | Delivery overview | `/delivery/` | Current UI | Rendered fixture UI |
| 11 | Delivery settings | `/delivery/settings/` | Current UI | Rendered fixture UI |
| 11 | Delivery origin form | `/delivery/origins/add/` | Modal proposal | Rendered fixture UI |
| 11 | Delivery area form | `/delivery/areas/add/` | Modal proposal | Rendered fixture UI |
| 11 | Provider form | `/delivery/providers/add/` | Modal proposal | Rendered fixture UI |
| 11 | Driver form | `/delivery/drivers/add/` | Modal proposal | Rendered fixture UI |
| 11 | WhatsApp settings | `/commerce/whatsapp/` | Current UI | Redirect destination; not an independent captured form |
| 11 | WhatsApp inbox | `/commerce/whatsapp/inbox/` | Current UI | Redirect destination; not an independent captured form |
| 11 | Audit workspace — enabled | `/audit/` | Current UI | Rendered fixture UI |
| 12 | Hosted storefront — enabled | `/shop/ui-fixture/` | Current UI | Rendered fixture UI |
| 12 | Storefront registration | `/shop/ui-fixture/account/register/` | Current UI | Rendered fixture UI |
| 12 | Storefront customer login | `/shop/ui-fixture/account/login/` | Current UI | Rendered fixture UI |
| 12 | Material stock history — open | `/inventory/#raw` | Current UI | Requested open/validation state; final screenshot verification pending |
| 12 | Receive purchases — open dialog | `/procurement/` | Current UI | Requested open/validation state; final screenshot verification pending |
| 12 | Customer form — server validation | `/sales/customers/add/` | Modal proposal | Requested open/validation state; final screenshot verification pending |
| 12 | Product selection — open | `/orders/add/` | Modal proposal | Requested open/validation state; final screenshot verification pending |

## Recovery artifacts

A local, git-ignored recovery package is saved at `test-results/figma-frontend-atlas/capture-recovery.zip`. It contains official capture payloads, route manifests, capture status, sample-UI screenshots and the temporary capture helpers. It contains fixture data, not live tenant records. The embedded capture IDs can expire; do not retry expired IDs or bypass the MCP quota.

The helper scripts are diagnostic artifacts, not a committed application feature. The temporary local gallery route and capture-script injection were removed. Restarting the helper requires an isolated fixture server and temporary local capture route, then valid fresh Figma capture IDs. Never aim these fixture tools at a live business database.

## Validation

- Disposable Django server startup system checks passed.
- Browser rendering exercised source templates at desktop/phone widths; source route/title manifests are included in the recovery package.
- Figma confirmed native text/layer imports and successful component/variable creation.
- Temporary source edits were restored; no application Python/template/style changes remain.
- Full Django functional/UI regression tests were not run: no production behavior was changed. This work does not certify every application flow as responsive or accessible.

## Existing context drift

Impeccable reported legacy PRODUCT.md schema and an out-of-date design sidecar. Its `init` and `document` workflows can refresh those records separately; they were not changed during this capture task.
