# INPROFIC Architecture

An index of how INPROFIC is put together, and why. INPROFIC is deliberately
small, but its domains are connected so procurement, production, sales and
finance describe one business flow rather than four isolated ledgers.

## What INPROFIC can be described as today

> **INPROFIC is a production-aware commercial management system for
> businesses that procure materials, prepare or manufacture finished goods,
> manage inventory, sell through multiple channels, and track the resulting
> payables, receivables and cash flow.**

It is **not yet a full manufacturing ERP**. It has a strong batch/recipe,
procurement, inventory, sales and finance foundation, but production planning,
MRP/material planning, WIP, scheduling, labour/overhead costing, formal QC
release gates. Delivery/last-mile fulfilment is now an entitlement-gated provider-neutral subsystem with an optional Glovo LaaS v2 plug-in.
See `docs/ROADMAP.md` for the remaining evolution.

Customer-facing ordering, external website/API routing, path-based tenant
storefronts and Distribution Market Stock integration are specified in
[`docs/COMMERCE_INTEGRATION.md`](COMMERCE_INTEGRATION.md).

## Directory layout

```
storetrack/
  manage.py
  storetrack/              project package: settings, root urls, wsgi
  apps/
    core/                   Business, base models, middleware, dashboard,
                            reports, backup and finance
    inventory/              RawMaterial, FinishedGood, RecipeItem,
                            ProductionMaterial, stock ledger/adjustments
    procurement/            PurchaseOrder, PurchaseOrderItem,
                            historical procurement cost snapshots, supplier payments
    production/             customer/physical orders, production batches,
                            yield/wastage, quality checks and frozen production costs
    sales/                  Customer master, Sale, SaleItem, customer payments
  templates/                one dir per app, mirroring the apps/ split
  docs/
    ARCHITECTURE.md         this file
    ROADMAP.md              remaining product evolution
  CLAUDE.md                 agent instructions
  .claude/skills/           project-local prompt skills for coding agents
  docs/skills/SKILL.md      prompt-skill index / usage guide
```


## Repository prompt skills

INPROFIC includes a project-local skill library under `.claude/skills/`,
modelled after the structured `SKILL.md` pattern used by larger agent-driven
Django projects. These skills capture INPROFIC-specific invariants so prompts
can load focused context without bloating every task.

The skills currently cover the full INPROFIC reference, simplification,
tenant safety, production integrity, finance integrity, migration safety and
systematic debugging. They are documentation/instruction assets only: Django
does not import them, they add no database tables, and they do not constitute a
runtime AI provider or end-user assistant.

See `docs/skills/SKILL.md` for the index.

## Core business flow

```
PurchaseOrder
    --receive-->
RawMaterial stock + procurement cost history
    --recipe/BOM + production inputs-->
Production Order
    --approve-->
Raw materials released/consumed
    --complete-->
ProductionBatch
    ├── gross output
    ├── wastage/rejected output
    ├── saleable output + yield
    ├── batch number + expiry
    ├── Quality Check
    └── ProductionCostSnapshot + material cost lines
              │
              ├── Physical Store stock
              └── Distribution / Online sale
                         │
                         ▼
                       Sale
                         │
                         ├── CustomerPayment
                         └── Receivable -> Received

Procurement -> SupplierPayment -> Payable -> Paid
All actual money movement -> FinancialTransaction -> CashAccount
```

Production orders are approved as a whole. Approval releases the material
requirements calculated from each finished good's recipe and production
materials. Completion records the actual production result rather than
assuming that planned quantity was perfectly achieved.

## Customer master data

`Sales.Customer` is the reusable customer record. It stores:

- name
- phone and email
- address
- region and customer group
- credit limit
- payment terms in days
- active/archive state
- notes

Distribution and Online production orders select a master customer. The order
still stores `customer_name`, `customer_region` and `customer_group` as a
historical snapshot so old invoices/reports do not change when a master
record is edited. Existing order/sale names are migrated into the customer
master when the migration runs.

## Product and customer-channel pricing

Selling-price resolution is intentionally layered and channel-specific:

```
Customer + Product + Channel price
            ↓ if unavailable
Product + Channel price
            ↓ if unavailable
FinishedGood.selling_price (default)
```

For Distribution and Online production orders, the selected customer may have
an agreed `CustomerProductPrice` for the selected finished good and channel.
That price has priority over the finished good's channel price. If no customer
agreement exists for that exact channel/product, the finished good's
`FinishedGoodChannelPrice` for that channel is used. If no channel price is
configured, the finished good's default `selling_price` is used.

The production-order form now receives these layers separately so its
Price/Unit display resolves the same way for the selected **channel + product**,
without accidentally falling back to another channel's price. Changing the
customer, order channel, or selected product immediately refreshes the
display. The resolved price is still snapshotted server-side onto
`OrderItem.price` when the order is saved, so historical orders are not changed
by later pricing edits.

Customer-specific pricing currently applies to Distribution and Online
channels. Physical Store orders do not use a customer price; their display
resolves channel price first and then the product default.

### "A user should be able to answer:"

### What price will this product use?
For a Distribution/Online order, the user should be able to select a customer
and product and immediately see the applicable unit price: **customer-specific
price → exact channel price → product default**. The saved order retains that
resolved price as a historical snapshot.

## Shared production runs (multi-customer / multi-product)

INPROFIC supports an optional `ProductionRun` above ordinary production
orders for bakery-style planning where several customer/store orders are
produced together in one coordinated exercise.

A Shared Run is **not a second recipe engine** and it does not assume that each
member order consumes a full standard batch. Every `OrderItem` keeps the same
proportional recipe logic used by ordinary approval. For example, if FAO Mini
Loaves yields 110 pieces from one registered batch recipe and an order requires
50 pieces, that order contributes `50 / 110` of each configured batch ingredient
and production input. A second order contributes its own exact fraction. The
Shared Run simply sums those normal requirements and releases the combined
stock in one approval.

The run can be assembled in either direction:

1. create a Draft Shared Run and attach any existing Pending orders intended
   for the same production exercise; and/or
2. from that Draft Run, create new customer/store Orders using the ordinary
   Order form. Each newly saved Order is automatically attached to the run.

This allows one run to contain multiple Distribution customers, Online
customers and Physical Store demand without merging their commercial records.
Each Order retains its customer, channel, requested quantity, optional planned
production quantity, current pricing hierarchy, discount, payment/receivable
state and eventual Sale.

While a Pending Order belongs to a Draft Shared Run, individual approval is
blocked so the same requirement cannot be released twice. Approving the run:

1. calculates every member OrderItem with its ordinary proportional
   batch/piece multiplier;
2. preserves flexible recipe overrides at the applicable product/order level;
3. aggregates identical raw materials across all member orders;
4. performs one combined stock-availability check and stock release;
5. writes the same per-order-item `OrderMaterialUsage` snapshots used by
   ordinary production costing; and
6. marks all member Orders Approved together.

Member Orders then use the existing completion workflow independently for
gross output, wastage, shortage, planned offcut, additional excess, QC and
Sales creation. Each resulting `ProductionBatch` links back to the
`ProductionRun`. The run is marked Completed when every member order is
completed. Orders that are not attached to a Shared Run continue through the
ordinary approval/completion path unchanged.

The older `ProductionRunMaterial` run-level material-substitution table is
retained only for database/history compatibility. New Shared Runs do not ask
for or apply common-material override quantities.

### A user should be able to answer:

- Which customer/store orders were coordinated in the same production run?
- What exact product quantities did each customer/order require?
- What proportional raw-material quantity did each order item contribute?
- What was the combined material release for the run?
- Which flexible recipe quantities were adjusted for a particular product?
- Which batches, customer sales, shortages, wastage and offcuts came from the
  shared run?

## Production batches, yield and wastage

Each completed production-order line creates one `ProductionBatch`.
A batch records:

- planned units
- gross units produced
- wastage/rejected units
- saleable units (`gross - wastage`)
- yield percentage (`saleable / planned * 100`)
- batch number
- expiry date
- wastage reason
- frozen total and unit production cost

The existing `FinishedGood.total_produced` now represents saleable production
from new batches, while wastage is kept separately on the batch and recorded
as a non-stock production-wastage movement. Physical-store stock and
customer-delivered totals increase only by saleable units.

For Distribution and Online orders, the customer/requested quantity is kept
separate from the production target. Completion may record less saleable output
as an explicit shortage, or more output as planned offcut/additional excess, so
the commercial demand is never silently rewritten by the physical result.
Physical-store production likewise records actual yield and wastage.

## Batch traceability

`ProductionBatch` is the traceability bridge between:

```
Customer/physical production order
        ↓
ProductionBatch
        ↓
ProductionCostSnapshot
        ↓
ProductionCostLine -> RawMaterial
        ↓
RawMaterialCostSnapshot -> PurchaseOrderItem -> PurchaseOrder
```

For Distribution and Online sales, `SaleItem.production_batch` also points
back to the production batch that fulfilled the sale. This permits a batch to
be traced forward into its originating customer sale and backward into the
materials/cost records used to produce it.

This is **production-batch traceability, not yet full supplier-lot
traceability**. Raw-material lots are not yet maintained as separate stock
pools, so INPROFIC should not claim exact lot-level genealogy until that
future layer is implemented.

## Quality control

`ProductionQualityCheck` is a one-to-one inspection record for each production
batch. It records:

- Pending inspection
- Passed
- Passed with conditions
- Failed
- inspector
- inspection time
- notes
- defects

QC is currently an explicit record attached to the batch, but it is **not yet
a hard release gate** that blocks stock/sales. That is intentional for this
stage so existing production workflows are not made brittle. A future QC
release workflow can be added once the business rules for quarantine,
rejection and rework are defined.

## Procurement and finance

Procurement supports Paid, Partially Paid and Unpaid states. A partially paid
PO records the amount paid immediately as a `SupplierPayment`; only the
remaining balance is shown as payable in Finance. Subsequent Finance payments
reduce the balance and the PO becomes Paid when fully settled.

Customer Distribution/Online orders use the same settlement pattern on the
other side:

```
Order: Receivable
      ↓ completion
Sale: Unpaid/Receivable
      ↓ Finance CustomerPayment(s)
Sale: Partially Paid -> Paid/Received
      ↓ final settlement
Order: Received
```

`FinancialTransaction` is the actual money-in/money-out ledger and
`CashAccount` is the account balance layer. The Order's customer payment state
is intentionally separate from the physical-store `transaction_type` field.

## Cost/profit/cash distinction

```
Sales revenue (paid transactions)
        - historical COGS
        = Gross profit

Cash received - actual cash outflows
        = Net cash flow

Unpaid product issues / receivables
        = non-cash activity tracked separately
```

Production cost is frozen at batch completion from the latest received
procurement cost available on the production date. It is not retrospectively
recalculated when a later procurement price changes.

## The Business (tenant) pattern

```
Request
  -> BusinessMiddleware reads the authenticated user
  -> validates active UserBusiness memberships
  -> resolves session active_business_id (or first active membership)
  -> request.business attached
  -> contextvar set (core.context)
  -> BusinessOwnedModel.objects filters every query to that business
  -> BusinessOwnedModel.raw_objects bypasses the filter for admin/shell
```

The global superuser can switch among all businesses. Ordinary users see and
switch only among businesses where their membership is active. The session
never accepts an arbitrary tenant ID without that authorization check.

Public signup atomically creates `Business`, the standard roles and role
permissions, all current `BusinessModuleAccess` entitlements, the first user,
and that user's Business Admin membership. Existing Business rows migrate as
the Bakery vertical so the original labels and behavior remain unchanged.

`Business.vertical` selects bakery, restaurant, general-production, wholesale,
or retail behavior without changing stable stock, sales, or channel keys.
Bakery and restaurant workspaces call the product definition a **Recipe**;
general production uses **Formula / BOM**. Restaurants additionally retain
service mode (dine-in, takeaway, delivery) and an optional table/service
reference.

Wholesale and retail are stock-first verticals. Their effective Production
module access is disabled by vertical capability while all historical
production rows remain intact. A purchase-order line may point to either a raw
material or a directly procured sellable product (exactly one). Receiving a
sellable product records a `fg_purchase` stock movement with supplier, PO,
quantity, date, and frozen unit cost. Retail sales use the stable Physical Store
channel. Wholesale stock releases reuse the stable Distribution channel so
customer-specific prices, credit terms, receivables, payments, and finance
reporting continue through the existing models.

The raw-material form includes a non-persistent conversion calculator. Standard
same-dimension conversions are deterministic; mass/volume conversions require a
user-supplied density in kg/L; local units such as spoons, caps, buckets, rolls,
or pallets require a measured custom ratio. Only applying the calculated ratio
to the form and saving changes business data. The stored conversion precision
was widened additively from two to six decimal places; existing values are
preserved.

`BusinessModuleAccess` sits above role and per-user permissions:

```
Business/plan enables module
          AND
Role/user grants action
          =
Effective access
```

Plan entitlements remain separate from vertical capability. Production remains
available to bakery, restaurant, and general-production businesses; it is
effectively unavailable to wholesale and retail while those verticals are
active. A later plan/pricing system can still change business entitlements
without altering role definitions or tenant-owned records.

## "A user should be able to answer:"

INPROFIC's intended value can be expressed as a chain of business questions:

### What did we buy?
**Procurement** records suppliers, purchase orders, received quantities,
purchase prices, historical material costs and supplier payments.

### What do we have?
**Inventory** tracks raw-material stock, finished-good shelf stock, stock
movements, adjustments, reorder levels and unit conversions.

### What can we make?
**Recipes/BOMs + production materials** define the material requirements for a
finished good, including packaging and production supplies.

### What should we make?
**Not fully yet.** Production orders can be created and material shortages can
be checked, but demand-driven production planning and MRP are roadmap items.

### What did we actually make?
**Production batches** record gross output, wastage/rejection, saleable output,
yield, batch number and expiry.

### What did it actually cost?
**Production cost snapshots** freeze material-based cost at the production
date. Labour and overhead costing remain roadmap items.

### What happened to quality?
**Quality checks** record the inspection outcome, inspector, defects and notes
for each production batch. Formal quarantine/release/rework control remains a
roadmap item.

### Where did it go?
Completed production can go to physical-store stock or directly to
Distribution/Online customers. Customer-order sale lines retain the production
batch used to fulfil them.

### Who bought it?
**Customer master data + sales/orders** provide reusable customer identity,
region/group, credit terms, order history and downstream sales records.

### Who owes us?
**Customer receivables** are tracked through Sales and Finance. Partial payments
remain outstanding; the final payment changes the sale and originating
Distribution/Online order to Received.

### Who do we owe?
**Supplier payables** are tracked from received purchase orders through
SupplierPayment records until the balance is cleared.

### Did we actually make money?
**Finance + sales + historical production cost** provide the foundation for
revenue, COGS, gross profit, cash flow and future product/channel/customer
profitability analysis.

## Modified and added files for the current production/commercial expansion

### Modified

- `apps/sales/models.py` — customer master, master links on sales/payments,
  production-batch link on sale lines.
- `apps/sales/forms.py` — customer master and customer form handling.
- `apps/sales/views.py` — customer master list/create/edit/archive workflows.
- `apps/sales/urls.py` — customer routes.
- `apps/sales/admin.py` — customer/batch-aware admin registrations.
- `apps/core/finance_views.py` — links Finance customer payments to the master
  customer when a linked sale has one.
- `apps/production/models.py` — customer link, ProductionBatch,
  ProductionQualityCheck and batch-linked cost snapshots.
- `apps/production/forms.py` — master-customer order selection and production
  completion/QC forms.
- `apps/production/views.py` — customer snapshot syncing, batch completion,
  yield/wastage handling, QC and batch traceability views; exposes separate
  default/channel pricing data for the production-order price resolver.
- `apps/production/urls.py` — batch and QC routes.
- `apps/production/admin.py` — batch/QC admin registrations.
- `apps/inventory/models.py` — production-wastage movement type.
- `templates/_nav_links.html` — Customers and production-batch navigation.
- `templates/production/order_form.html` — customer master selection and
  restored channel-aware Price/Unit auto-rendering with customer/channel/default
  resolution.
- `templates/production/order_detail.html` — batch links and new completion
  workflow.
- `templates/sales/sales_list.html` — existing sales UI remains compatible with
  the master/batch model.

### Added

- `templates/sales/customers_list.html`
- `templates/sales/customer_form.html`
- `templates/production/order_complete.html`
- `templates/production/batches_list.html`
- `templates/production/batch_detail.html`
- `apps/sales/migrations/0002_customer_master.py`
- `apps/sales/migrations/0003_saleitem_production_batch.py`
- `apps/production/migrations/0003_production_batches_customer_links.py`
- `apps/inventory/migrations/0002_production_wastage_movement.py`
- `docs/ROADMAP.md`

The inventory migration is intentionally schema-neutral because adding a
choice value does not require a database column change; it simply gives the
migration history an explicit marker for the new movement type.

## Update this file when

- An app boundary changes or a new app is added.
- The Business/tenant pattern changes.
- A new stock-mutating flow is added.
- A production/sales/finance relationship changes.
- A major capability moves from roadmap into the implemented product.


### Automatic uncommitted planned-offcut stock
For customer orders with an explicit production target above the ordered quantity, saleable planned offcut no longer has to be manually allocated in full at completion. Any quantity not already committed to an identified Distribution/Online customer is automatically retained as general FinishedGood stock, so production completion is not blocked while waiting for a future buyer. Finished Goods inventory exposes a conservative `Uncommitted Offcut` figure: unreversed planned offcut retained to stock, less explicit shortage reconciliations, capped by the live physical stock balance.

### Reversed orders: recreate rather than mutate
A reversed order is an immutable historical correction record. `Edit as New Order` copies its customer/channel snapshot, order lines, production-plan quantities and discounts into a fresh Pending order dated today. The new order then follows the current normal lifecycle and may be attached to a draft Shared Production Run. This avoids reactivating reversed stock/finance history while still making error correction practical.

### Business-specific production order numbering

Production Orders separate the global database primary key (`Order.id`) from the human-facing `Order.order_number`.

- `Order.id` remains global, immutable and is used for URLs, foreign keys and technical references.
- `Order.order_number` is unique only within a Business (`business + order_number`).
- `OrderNumberSequence` stores the next visible number independently for each Business.
- A Business Admin may reset only their own sequence, and only when that Business has no remaining Order rows.
- A global superuser may reset a selected Business under the same rule, or reset all Business sequences when no Order rows exist anywhere.
- Sequence reset never truncates or resets the database primary-key sequence and does not modify users, inventory, customers, finance, recipes, stock movements, Shared Production Runs or other business data.

This is the numbering foundation for multi-tenant operation: two businesses may each legitimately have an `Order #1` while their internal database IDs remain globally distinct.


### Repeatable planned-offcut customer allocations

At production completion, saleable planned offcut may be split across any number of interested Distribution/Online customers. Each allocation records customer, channel, quantity, and its own linked Sale/receivable in `ProductionOffcutAllocation`. The batch keeps aggregate customer-allocation units plus the automatic remainder retained as general finished-goods stock. The original single-customer batch fields remain available for batches completed before this structure.

If no customer is available, all saleable planned offcut automatically remains in general finished-goods stock. If several customers are available, the sum of their allocations may not exceed the saleable planned offcut; any remainder still becomes general stock.

Shared Production Run creation uses a multi-select dropdown of eligible Pending orders rather than a checkbox list. The run still supports adding new customer Orders from its detail page and aggregates each member Order's normal proportional recipe/material requirement at approval.

## Commerce and subscriptions (2026-09)

INPROFIC now has an additive `commerce` app and an account-level subscription entitlement layer. Commerce uses `CommerceIntake` as the only public/external write boundary; API/storefront callers never write Production, Sales, stock or Finance directly. See `docs/COMMERCE_INTEGRATION.md`.

Commercial subscriptions are translated into explicit `BusinessModuleAccess` rows. Existing businesses without a `BusinessSubscription` remain fully enabled until they opt in; new signups begin a 30-day STARTER trial. Additional service lines are separate `Business` profiles linked by `SubscriptionService` under the same commercial subscription. See `docs/SUBSCRIPTIONS.md`.

## Subscription checkout and commerce integration surfaces (September 2026 refinement)

Subscription billing is initiated inside INPROFIC. Paystack and Monnify are hosted-checkout providers: INPROFIC creates the subscription payment request, initializes checkout server-side, redirects the tenant to the provider, then verifies callbacks/webhooks server-side before extending entitlements. Provider secrets live in deployment environment variables, not templates or tenant websites.

Plans support monthly and yearly billing. The founder controls monthly price, yearly discount percentage, and additional-service discount percentage centrally. The annual discount applies to the complete 12-month subscription total after additional-service pricing is calculated.

Commerce remains payment-provider neutral at the intake boundary. The four independently switchable public surfaces are the hosted storefront, Order Now link, headless API, and platform connector (all beneath the Commerce master switch and BusinessModuleAccess entitlement). Headless API is for a tenant-owned website/app actively calling INPROFIC; the connector endpoint is for third-party platforms/adapters pushing signed normalized order events into INPROFIC.


## Audit and delivery boundaries

Audit Workspace and Delivery are separate plan entitlement modules. Their
`BusinessModuleAccess` rows are evaluated before user or role permissions, so
founder plan toggles remain the commercial hard ceiling.

External auditors are intentionally not granted ordinary operational module
permissions. Audit Workspace performs tenant-scoped read-only queries internally
and exposes evidence, exports and auditor query/response records through a
specialized surface.

Delivery remains provider-neutral. INPROFIC owns delivery quotes, delivery fee
snapshots, assignments, events, proof of delivery and customer tracking.
Provider accounts, including the seeded Glovo LaaS v2 account, add optional live provider quotes, OAuth parcel dispatch, tracking links and authorized webhook synchronization without making the rest of the system Glovo-specific.

In-Premise POS is a separate `pos` permission. The system role `pos_operator` has no Dashboard permission and therefore lands directly in the full-page POS, while the same `pos` permission can also be granted as a supplemental per-user override to managers or other staff who should retain their primary role.


### Delivery rider and storefront-customer boundaries

Delivery Rider is a purpose-specific staff access surface: rider-only users can see and act only on assignments linked to their own active in-house driver record. Delivery alerts reuse the durable Commerce notification transport but support targeted rider recipients. Public storefront customers remain guests by default; optional `StorefrontCustomer` profiles are tenant-scoped, separate from staff users, and store purchase history without making registration a checkout requirement. Hybrid delivery means in-house and the configured provider remain interchangeable per order under the tenant’s routing and customer-visible post-payment switch policy.
