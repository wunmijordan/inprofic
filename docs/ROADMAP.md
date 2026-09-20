# INPROFIC — Remaining Product Evolution

This document is the forward plan **relative to what is already implemented**.
It intentionally excludes the completed customer master, production
yield/wastage, production-batch traceability and first-stage quality-control
work. It also accounts for the Finance changes already implemented: supplier
partial payments/payables and Distribution/Online receivable settlement.

The aim is not to turn INPROFIC into a generic ERP. The recommended direction
is a focused **production-to-commerce system for small and mid-sized businesses
that prepare or manufacture products before selling them**.

## 1. Production intelligence — next priority

### 1.1 Production planning

Introduce a first-class `ProductionPlan` / production planning workflow that
can answer:

- What customer demand is expected?
- What finished goods are already in stock?
- What is already being produced?
- What should be produced next?
- By when?

Suggested flow:

```
Orders / demand + current stock + production already planned
                    ↓
             Production Plan
                    ↓
          Planned production quantities
                    ↓
              Production Orders
```

Do not replace the existing Order workflow. Add planning above it.

### 1.2 Material Requirements Planning (MRP-lite)

Use recipes/BOMs, planned production, current raw-material stock and reorder
levels to calculate:

```
Required material
      - available material
      = shortage
```

Then show suggested procurement rather than automatically creating POs.
The first version should be advisory and reversible.

### 1.3 Production queue/dashboard

Give production staff a single view of:

- pending production
- approved/material-released production
- today's planned batches
- overdue production
- material shortages
- batches awaiting QC
- recent yield/wastage

## 2. Production control

### 2.1 WIP and production stages

The current system records a production order as a whole. Add stages only if
the target businesses genuinely need them, e.g.:

```
Material issue → Preparation → Processing → QC → Packaging → Finished
```

Track WIP separately from raw material and finished goods.

### 2.2 Production scheduling

Add planned and actual start/end times, priority, shift/work centre and,
where useful, production capacity.

Do not introduce machine/work-centre complexity until multiple simultaneous
production jobs actually create capacity conflicts.

### 2.3 Rework and rejection

Extend QC so a failed batch can be:

- quarantined
- partially accepted
- rejected
- sent for rework
- scrapped

This should affect saleable inventory only through an explicit release action.

## 3. Production economics

### 3.1 Labour costing

Add optional labour cost to production batches, initially at the simplest
useful level:

- fixed labour cost per batch, or
- labour hours × rate.

Only introduce employee-level time tracking when there is a real operational
need for it.

### 3.2 Production overhead

Add optional batch/period overhead allocation for costs such as:

- electricity
- production rent allocation
- equipment usage
- gas/utilities not already represented as a production material

Keep direct material cost separate from labour and overhead so reports can
show the components.

### 3.3 Actual vs expected cost

Once labour/overhead exists, show:

```
Expected material cost
Actual material cost
Labour
Overhead
Total actual batch cost
Cost per saleable unit
Variance
```

### 3.4 Product/channel profitability

Build profitability reports around:

```
Revenue
- actual historical production COGS
- discounts
- directly attributable delivery/selling costs
= contribution margin
```

Break this down by product, channel, customer and period.

## 4. Full batch/lots traceability

The current system has **production-batch traceability**. The next step is
supplier-lot genealogy.

### 4.1 Raw-material lots

Introduce a first-class `RawMaterialLot` / received-lot model with:

- supplier
- purchase order/item
- lot/batch number
- received date
- expiry date where applicable
- quantity received
- remaining quantity
- unit cost

### 4.2 Consumption allocation

Production consumption should identify which raw-material lots were consumed.
Only then can INPROFIC honestly answer:

> Which supplier lot went into this finished-good batch?

### 4.3 Forward traceability

From a raw-material lot:

```
Supplier lot
  ↓
Production batches
  ↓
Finished goods
  ↓
Sales / customers
```

This is particularly valuable for food, cosmetics and other regulated or
expiry-sensitive products.

## 5. Quality-control maturity

The current QC record is intentionally non-blocking. Next steps:

1. configurable inspection checklist by finished good
2. measured values (weight, temperature, dimensions, etc.)
3. pass/fail limits
4. quarantine status
5. explicit release-to-sale action
6. rejection/rework workflow
7. QC history and defect analytics

Do not hard-code food-specific tests into the core model. Use configurable
checks if this layer is built.

## 6. Commercial management

### 6.1 Customer master depth

The customer master is now implemented. Remaining useful depth:

- customer-specific price lists
- customer-specific payment terms enforcement
- credit-limit warnings
- overdue receivable ageing
- customer statement
- customer sales/profitability history
- territory/customer-group performance

### 6.2 Distribution fulfilment

Extend the current Distribution order → Sale flow with:

```
Order
 ↓
Production / stock allocation
 ↓
Pick / pack
 ↓
Dispatch
 ↓
Delivery confirmation
 ↓
Invoice / receivable
 ↓
Payment
```

Potential fields/entities:

- delivery status
- dispatch date
- delivery date
- driver/courier
- vehicle
- delivery reference
- returned quantity
- damaged quantity
- proof of delivery

### 6.3 Online fulfilment

Extend Online orders beyond order/payment recording into:

```
Paid/receivable order
 ↓
Fulfilment
 ↓
Packing
 ↓
Dispatch
 ↓
Delivery
```

Keep payment state separate from fulfilment state.

## 7. Finance — remaining evolution

### Already implemented

The following Finance work is considered done and should not be rebuilt:

- supplier partial-payment amount on procurement forms
- initial `SupplierPayment` for partially paid POs
- remaining supplier balance in Finance
- later supplier payments reducing that balance
- PO status becoming Paid when fully settled
- Distribution/Online customer receivables visible in Finance
- partial customer payments
- final customer payment changing the Sale to Paid/Received
- final customer payment changing the originating Distribution/Online Order
to Received
- `FinancialTransaction` remaining the actual cash ledger

### 7.1 Receivable ageing

Add:

- current
- 1–30 days
- 31–60
- 61–90
- 90+

based on order/payment terms and due dates.

### 7.2 Payable ageing

Mirror the same structure for suppliers.

### 7.3 Customer statements

Generate a statement containing:

```
Opening balance
Invoices/sales
Payments
Adjustments
Closing balance
```

### 7.4 Supplier statements

Mirror customer statements for supplier obligations.

### 7.5 Stronger accounting linkage

If the product later needs formal accounting rather than operational cash
management, introduce explicit journal/ledger concepts instead of trying to
make `FinancialTransaction` do double-entry accounting by itself.

Possible future structure:

```
Business event
    ↓
Journal entry
    ↓
Debit / Credit lines
    ↓
General ledger
```

This should only be undertaken if financial/accounting requirements justify
it.

## 8. Inventory evolution

### 8.1 Inventory locations / multi-location

`InventoryLocation` already exists, but real operational routing is not yet
the central model.

Future work:

- multiple stores/warehouses
- stock by location
- transfers
- location-specific reorder levels
- production location
- dispatch location

This should be designed together with the Business/tenant routing work rather
than implemented as isolated fields.

### 8.2 Stock reservation

For distribution/online orders, reserve available finished stock where an
order can be fulfilled from inventory rather than requiring new production.

This becomes especially useful after production planning exists.

### 8.3 Returns

Formalize customer and supplier returns so returned goods have explicit
commercial, stock and financial consequences.

## 9. Forecasting and commercial intelligence

Once the core transaction history is reliable:

- sales forecasting
- demand by product/channel/customer
- material demand forecasting
- seasonality
- slow-moving stock
- stockout risk
- production capacity utilization
- wastage trends
- yield trends
- margin trends

Avoid AI/forecasting complexity until enough clean historical data exists.

## 10. Multi-business / multi-location architecture

Multi-business SaaS routing by authenticated membership and session selection
is implemented, including public Business Admin signup, per-business branding,
vertical profiles, business-level module entitlements, and tenant-safe scoped
queries. SQLite remains the supported PythonAnywhere deployment database.

Remaining scale work includes:

- optional subdomain/custom-domain routing
- first-class locations and inventory transfers within a business
- subscription plans/prices that manage `BusinessModuleAccess`
- possibly dedicated databases for enterprise isolation

should be added deliberately.

## 11. Roles and permissions

The current project is intentionally simple. A production business will
probably eventually need:

- owner/admin
- production manager
- production operator
- inventory manager
- procurement officer
- sales officer
- finance officer
- read-only/reporting user

Permissions should be action-based, especially for:

- approving production
- forcing negative stock
- recording payments
- changing historical transactions
- changing recipes/costing
- recording QC results
- exporting/backup

## 12. Payment integration — deliberately deferred

Do **not** build the Moniepoint integration as a parallel bookkeeping system.
When ready, integrate payment infrastructure into INPROFIC's existing
Finance model.

Preferred architecture:

```
INPROFIC order
      ↓
Payment request
      ↓
Payment provider / POS
      ↓
Payment confirmation
      ↓
CustomerPayment / FinancialTransaction
      ↓
Receivable -> Received
```

Potential future integrations include Moniepoint POS/payment rails and
Monnify for online payments. The provider should confirm payment; INPROFIC
should remain the business system of record.

## 13. Recommended implementation order

### Next

1. Production planning
2. MRP-lite/material shortage planning
3. Production dashboard/queue
4. Yield/wastage analytics
5. Product/channel profitability

### Then

6. WIP/stages where needed
7. Production scheduling
8. Labour/overhead costing
9. Mature QC/release/rework
10. Distribution fulfilment
11. Online fulfilment

### Then, when operational scale justifies it

12. Raw-material lots and full genealogy
13. Inventory transfers/multi-location
14. Receivable/payable ageing and statements
15. Customer/supplier statements
16. Returns
17. Forecasting
18. Subscription plans and pricing
19. Tenant location/inventory-transfer routing

### Later / optional

20. Moniepoint/Monnify payment integration
21. Formal double-entry accounting layer
22. Advanced forecasting/automation

The ordering is deliberate: **first make INPROFIC know what should be
produced, then make it explain how efficiently it was produced, then make it
control fulfilment and financial consequences, and only afterward add scale
and external integrations.**


## Implemented in this release

- Product categories exposed across Inventory, POS, hosted storefront and product API.
- Controlled measurement-change workflow for current-balance conversion without rewriting historical records.
- Plan-gated Delivery with in-house, Hybrid and provider-neutral custom courier paths, plus the optional Founder-gated Glovo LaaS v2 live-quote/OAuth/webhook adapter.
- Plan-gated Audit Workspace with external auditor role, read-only evidence views, Excel export and query/response handling.
- Dedicated POS-only access flow that sends cashier-only users directly to the in-premise storefront, plus supplemental POS permission for staff who retain another primary role.


### Delivery rider and storefront-customer boundaries

Delivery Rider is a purpose-specific staff access surface: rider-only users can see and act only on assignments linked to their own active in-house driver record. Delivery alerts reuse the durable Commerce notification transport but support targeted rider recipients. Public storefront customers remain guests by default; optional `StorefrontCustomer` profiles are tenant-scoped, separate from staff users, and store purchase history without making registration a checkout requirement. Hybrid delivery means in-house and the configured provider remain interchangeable per order under the tenant’s routing and customer-visible post-payment switch policy.
