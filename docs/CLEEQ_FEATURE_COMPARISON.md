# INPROFIC and Cleeq: feature-by-feature comparison

Reviewed 18 November 2026. This is an internal product assessment, not a public
claim that a competitor lacks a capability. Cleeq evidence comes from its public
website; INPROFIC evidence comes from the current repository. Neither a Cleeq
account nor a hands-on performance/usability comparison was used.

**Assessment:** INPROFIC has a stronger implemented foundation for production-led
businesses and connected ordering/fulfilment. Cleeq advertises broader retail
checkout, branch and device operations. These are workflow-fit judgments, not
benchmarked quality scores. “Not documented” means the reviewed public sources
do not establish the feature; it does not mean the feature is absent.

## Implementation follow-up

The comparison below records the initial baseline. INPROFIC now includes branch
stock transfers/receipt confirmation, cashier sessions, cash plus verified-transfer
tenders, barcode/CSV onboarding, parked baskets, accepted customer quotations,
product promotions, personal profiles and device controls. Prepared offline
packages include the Basic dashboard, cash POS with local receipts, stock reference,
expiry flags and delivery assignment, with explicit confirmed replay. Expiration
is advisory, including blank dates. See [the implementation guide and limits](RETAIL_AND_OFFLINE.md).
Loyalty points, gift cards, cashier exchanges, staff PIN login, specialist serial
tracking and certified printer/device transport are still outside this change.
The tables are baseline research, not a fresh competitive test of these additions.

## Sources and availability

- [Cleeq features](https://cleeq.ng/features/) — POS, stock, finance, staff,
  reports and future manufacturing/accounting scope.
- [Cleeq pricing](https://cleeq.ng/pricing/) — plan restrictions and add-ons.
- [Cleeq FAQ](https://cleeq.ng/faq/) — offline sync, devices, printing and setup.
- [Cleeq guides](https://cleeq.ng/guides/) — stock, cash-control and retail guidance.
- INPROFIC: [README](../README.md), [product context](../PRODUCT.md),
  [feature catalogue](feature_marketing_catalog.json), and the implementation
  references below.

Cleeq labels both its **Manufacturing** and **Accounting** add-ons **coming soon**.
Their detailed descriptions are roadmap evidence, not proof of availability.
Its pricing page also describes reconciliation both as a yearly Deluxe benefit
and as a separately priced yearly Deluxe add-on; confirm the commercial terms
before using them in a purchasing comparison. This assessment does not rank
subscription value because INPROFIC pricing is configurable and plan scope differs.

## Production, inventory and procurement

| Capability | INPROFIC today | Cleeq public offering | Assessment |
| --- | --- | --- | --- |
| Production approval and completion | Implemented material release, output, batches and audited reversal | Manufacturing add-on is coming soon | INPROFIC has an availability advantage for producers |
| Recipe quantities and yield | Purchase/package/usage conversions; proportional recipe consumption against registered yield | Future manufacturing describes recipes, yield and expected loss | INPROFIC implements the core workflow today |
| Shared production | Shared Runs coordinate separate customer orders and attributable material usage | Equivalent shared-run behavior not documented | INPROFIC has demonstrated depth; Cleeq equivalence unverified |
| Offcuts and production variance | Separates customer demand, planned offcut, excess, wastage and shortage | Future manufacturing describes by-products, rework and scrap | INPROFIC's implemented allocation/reconciliation is a strength |
| Advanced manufacturing planning | No verified work-centre scheduling, nested recipe planning or versioned recipe workflow | Future scope includes work centres, recipe versions, nested components and material planning | Useful future ideas, not a current Cleeq advantage |
| Production conversion costs | Material costing; Books explicitly excludes capitalising conversion costs beyond materials | Future scope describes labour/overhead absorption | INPROFIC improvement opportunity; no compliance comparison inferred |
| Batch expiry allocation | Market Stock allocates earliest expiry first, with dated receipt/expiry checks | Advertises batch/expiry tracking and earliest-expiry checkout on Grow+ | Both have expiry controls; coverage differs by stock workflow |
| Supplier purchasing | Requests, approvals with reasons, grouped suppliers, partial receipts and separate advances | Advertises supplier orders, receipts and supplier-grouped reorder lists | Both cover purchasing; supplier-grouped reorder suggestions are worth evaluating |
| Customer/channel pricing | Customer + product + channel agreements, saved invoice prices and selling portions | Advertises pricing, minimum selling prices and units | INPROFIC has demonstrated distribution pricing depth; exact Cleeq equivalence unverified |
| Branch stock operations | Business profiles, inventory locations and Market/Physical transfers; no verified unified branch ledger with receipt confirmation | Advertises branch/warehouse stock, pick lists, waybills and receiving confirmation | Cleeq has a clearer branch operations proposition |
| Retail stock counts | Adjustments and ledger cut-over count import | Advertises stock-count variance review/approval | Cleeq advertises a more complete recurring stocktake workflow |
| Product imports | No verified general product CSV onboarding wizard | Advertises CSV import with flexible column names | Cleeq advertises an onboarding advantage |
| Serial numbers / IMEI | No verified per-unit serial lifecycle | Advertises receipt-to-sale unit tracking | Cleeq advertises an electronics-retail advantage |
| Restricted goods | Module/user permissions; no verified prescription/ID sale evidence workflow | Advertises controlled-item staff permissions and sale evidence on Grow+ | Cleeq advertises a specialist retail advantage |

## Checkout, customer experience and fulfilment

| Capability | INPROFIC today | Cleeq public offering | Assessment |
| --- | --- | --- | --- |
| Selling without internet | PWA installation/offline fallback; deliberately does not store live business data offline | Advertises locally saved sales, automatic sync and duplicate prevention | Cleeq's most material advertised advantage for unreliable connectivity |
| Barcode checkout | No verified scanner/camera or barcode-label workflow | Advertises scanners, camera scanning and label printing | Cleeq advertises a faster retail entry workflow |
| Receipt hardware | Browser receipt printing exists; no verified dedicated Bluetooth/thermal bridge | Documents thermal/A5/A4 printing, Android/Bluetooth and cash-drawer support | Cleeq documents broader hardware support; test compatibility before choosing |
| Split tenders | Split customer-payment recording exists; Commerce POS selects one method at checkout | Advertises mixed cash/transfer/card in one sale | Cleeq's advertised advantage is specifically mixed-tender POS |
| Cashier sessions | Dedicated POS role and permissions; no verified drawer opening/count/closing workflow | Advertises shifts, cash over/short and checkout opening/closing reports | Cleeq advertises stronger daily cashier controls |
| Parked sales / collection deposits | Customer orders, credit and part payments; no verified cashier parked-basket workflow | Advertises held sales and deposits for collection | Cleeq advertises a useful busy-counter workflow |
| Loyalty and promotions | Customer prices and product options; no verified points, gift cards or automatic promotion rules | Advertises loyalty, gift cards and scheduled offers | Cleeq advertises stronger repeat-purchase tools |
| Returns and exchanges | Distribution returns, credits, redistributable stock/damage and compensating corrections | Advertises refunds, exchanges and difference settlement | Different strengths; INPROFIC should consider a unified cashier exchange flow |
| Accepted customer quotations | Shareable production proposals that prefill an order; not customer acceptance contracts | Advertises customer quote acceptance and invoice proof upload by link | Cleeq advertises a distinct sales-conversion workflow worth adopting |
| Hosted commerce and website API | Hosted storefront, variants/add-ons, verified checkout and headless/connector boundaries | Comparable hosted storefront/API not established by reviewed pages | INPROFIC has a demonstrated capability advantage; Cleeq scope unverified |
| WhatsApp selling | Tenant-owned WhatsApp Storefront with structured commerce handoff; production setup depends on Meta permissions | Documents WhatsApp receipt sharing, not equivalent conversational ordering | INPROFIC has broader implemented commerce scope; sharing is not unique |
| Delivery operations | Live quotes, in-house/external/Hybrid methods, rider batches, tracking, ETA and proof of delivery | Equivalent last-mile workflow not documented | INPROFIC has demonstrated fulfilment depth |

## Finance, staff and management

| Capability | INPROFIC today | Cleeq public offering | Assessment |
| --- | --- | --- | --- |
| Customer credit | Limits, payment terms, outstanding invoices and settlements | Advertises credit limits and collections | Shared capability; no overall winner established |
| Statement reconciliation | CSV import and ledger matching with adjusted-balance proof | Advertises bank/POS matching and settlement reports | Shared capability; Cleeq's settlement-specific reports need a direct comparison |
| Double-entry Books | Implemented chart, balanced entries, manual journals/reversals, trial balance and statements via explicit rebuild | Full accounting add-on is coming soon | INPROFIC has an implementation advantage, not automatic continuous posting |
| Tax / e-invoicing | Configurable VAT and e-invoice preparation; provider submission/clearance is separate | Advertises VAT and planned accounting; equivalent e-invoice clearance not established | Do not claim either product guarantees regulatory compliance |
| Payroll | Staff payroll add-on, rules, funding and shareable payslips | Equivalent payroll workflow not documented | INPROFIC has demonstrated scope; salary expenses alone are not payroll |
| Device restrictions / quick staff login | User/role permissions and notification device registration; no verified registered-POS-device allowlist or staff PIN flow | Advertises staff code/PIN, branch restrictions and device revocation | Cleeq advertises stronger counter-specific access controls |
| Internal team chat | Assistant and delivery messaging; no verified general staff chat | Advertises internal team messages | Cleeq advertises a collaboration addition, separate from INPROFIC's assistant |
| Reporting | Operational reports, dashboards, Daily brief, Books/tax and audit evidence | Advertises 109 reports, branch filters and exports | Cleeq advertises breadth; a report count does not establish accuracy or usefulness |
| Help and onboarding | Guided tour, contextual field help and assistant | Advertises setup checklist and guides for every screen | Both have help; adopt task-specific completion guidance rather than assume a usability winner |

## Adoption priorities

These are recommendations, not implemented features or measured ROI claims.

1. **Cashier opening/closing and mixed tenders.** Build a small, auditable POS
   session model: opening float, tender totals, counted cash, variance reason,
   manager review. Reuse current Finance settlement services and prevent duplicate
   receipts. This improves the existing retail surface without replacing it.
2. **Barcode entry and reliable receipts.** Add product identifiers, keyboard
   scanner input, camera fallback and barcode labels. Validate 58/80 mm layout,
   printer transport and paper cutting separately; a printable page is not proof
   of Bluetooth or cash-drawer compatibility.
3. **Product CSV onboarding.** Preview column mapping, units, prices, stock source
   and validation errors before an audited import. Route opening stock through
   existing ledger/stock services rather than silently assigning balances.
4. **Offline POS discovery, then a bounded pilot.** This is high value but high
   complexity. Begin with a specific stock source, local transaction identifiers,
   visible pending-sync state, duplicate prevention, device revocation, stock
   conflict handling and recoverable retries. Never present an offline transfer
   claim as verified electronic payment. Keep broader Finance/production writes
   outside the initial pilot.
5. **Real branch and warehouse operations.** Treat this as domain work, not a
   renamed business switcher: per-location balances, authorised transfers,
   dispatch/receipt discrepancies, branch staff and consolidated reporting.
   Keep separate tenant businesses isolated.
6. **Customer quotations and cashier exchanges.** Add explicit acceptance,
   expiry, conversion and returned/replacement value settlement. Reuse existing
   pricing snapshots, returns and compensating finance behavior.
7. **Loyalty and promotions after POS controls.** Define stacking, margins,
   returns, expiry and gift-card liability before adding marketing controls.
8. **Selective specialist expansion.** Serial/IMEI and restricted-item evidence
   make sense if electronics/pharmacy are intentional target markets. Work-centre
   planning, recipe versions and labour/overhead costing fit a producer roadmap,
   but Cleeq's future descriptions are not validated implementations to copy.

For INPROFIC's current bakery/production/distribution focus, preserve production
depth and improve counter operations first. For a deliberate multi-branch retail
push, offline selling and branch inventory move to the front of discovery.

## Implementation evidence and limits

- `apps/production/models.py`, `apps/production/views.py` — order/run/batch,
  flexible usage, completion, quality and reversal workflows.
- `apps/inventory/models.py`, `apps/inventory/services.py` — conversions, portions,
  locations, movements and expiry-ordered allocation.
- `apps/core/books.py` — derived Books and explicit costing simplifications.
- `apps/core/bank_reconciliation.py` — statement parsing/matching and balance proof.
- `apps/core/finance_forms.py` — scoped customer payments and split-payment entry.
- `templates/commerce/storefront_pos.html`, `apps/commerce/payment_services.py` —
  POS tender selection, receipts and settlement boundaries.
- `apps/commerce/checkout_services.py`, `apps/commerce/delivery_services.py`,
  `apps/whatsapp_storefront/` — commerce, last-mile and messaging workflows.
- `apps/accounts/models.py` — memberships, subscription services and payroll.
- `templates/pwa/offline.html`, `apps/core/pwa.py` — intentionally limited offline behavior.

Repository inspection verifies implementation presence, not every deployment's
configuration, subscription entitlement or operational readiness. Cleeq claims
still need a hands-on demo, especially offline recovery, cross-branch transfers,
hardware, manufacturing/accounting release status and reconciliation billing.

The retail interface now follows the shared workspace: standalone editable
profiles, rounded list tables, dynamic quotation rows and the same storefront
document layout for quotations. Buy/get campaigns select X and Y product sets
separately. Prepared offline screens reuse the live templates and navigation,
with cash POS and rider assignment restricted to the prepared packages.
