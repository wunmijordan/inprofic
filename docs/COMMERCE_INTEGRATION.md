> **Current website integration contract:** New and migrated headless integrations must use the pre-intake checkout/payment boundary documented in [`WEBSITE_COMMERCE_CHECKOUT_INTEGRATION.md`](WEBSITE_COMMERCE_CHECKOUT_INTEGRATION.md). `POST /orders` is retired and returns HTTP 410; no new public or connector write may create an operational intake before verified payment.

# Customer Ordering and External Commerce Integration

## Architectural intent

INPROFIC should remain the system of record for tenant identity, products,
pricing, production, inventory, sales, receivables and cash. Customer-facing
ordering should be built into the same Django application and database, while
the same ordering capability is exposed through a stable integration boundary
for existing websites and third-party ordering platforms.

These are complementary entry points, not separate ordering systems:

```text
INPROFIC customer storefront ----+
                                    |
Existing business website ----------+--> Shared commerce/order service
                                    |             |
Third-party ordering platform ------+             v
                                          Existing production,
                                          inventory, sales and finance
```

The shared commerce service is responsible for tenant resolution, product and
quantity validation, server-side pricing, idempotency, customer/service data,
availability and routing. INPROFIC's own storefront can call it directly.
External systems call the same contract through an API or signed webhook.

External systems must never write directly to `production.Order`, `sales.Sale`,
stock balances or finance records. Direct writes could bypass price snapshots,
material release, batch costing, expiry, stock allocation, receivables and audit
rules.

Delivery follows the same rule. A website may request a delivery quote and pay
the full checkout amount, but delivery assignments are created only after trusted
payment verification. The delivery engine then owns in-house, hybrid, manual
third-party and optional Glovo LaaS v2 live-quote/dispatch without allowing the storefront
to create operational delivery records directly.

Product categories are part of the public product contract. They are business-defined
records and do not replace the source distinction between made-in-house and
purchased-for-resale products.

## Supported ways to plug INPROFIC into another website

### 1. Hosted storefront

Each tenant can have a public path such as:

```text
https://app.example.com/shop/blue-kitchen/
```

An existing website only needs an **Order now** link. INPROFIC owns catalogue
validation and checkout, so the host website requires no complex integration.
This should be the first delivery because it has the lowest compatibility and
deployment risk.

### 2. Embeddable catalogue or order button

A small JavaScript component can display INPROFIC products on an existing
site. Checkout should still open the hosted INPROFIC route. The tenant's site
controls placement and presentation; INPROFIC remains authoritative for
prices, availability and order creation.

### 3. Headless API

Custom websites and mobile applications can consume a versioned contract:

```text
GET  /api/v1/storefronts/{business_slug}/products
POST /api/v1/storefronts/{business_slug}/checkouts
POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments
GET  /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments/current
GET  /api/v1/storefronts/{business_slug}/orders/{public_id}   # only after materialization
```

Order submission should require an idempotency key. Product and order
identifiers exposed publicly should be non-sequential UUIDs. Submitted totals
must be ignored and recalculated from INPROFIC pricing.

The human-facing `number` is generated from a locked per-business sequence and
may therefore repeat across different tenants (for example, each tenant may
have `WEB-000001`). Integrations must identify an order by `business_slug` plus
its INPROFIC `id` UUID, never by `number` alone.

The product payload also exposes customer-facing metadata such as `image_url`
and an `order_modes` array containing vertical-specific labels, INPROFIC
channel prices, fulfilment routes, and per-mode quantity limits.

### 4. Platform connectors

Shopify-, WooCommerce- or restaurant-platform-style connectors translate each
provider's signed webhook payload into the same INPROFIC intake contract.
Provider order IDs must be unique per tenant/integration so retries cannot
create duplicate INPROFIC orders.

## Customer-order intake boundary

A public submission should first create a tenant-owned commerce intake record,
not immediately mutate production, shelf stock or finance. The intake record
can contain:

- public order UUID and customer-facing number;
- source/integration and external order ID;
- customer/contact snapshot;
- requested products and quantities;
- INPROFIC-calculated price snapshots;
- service mode, table/reference, delivery or pickup details;
- payment state kept separately from fulfilment state;
- links to the accepted production order, sale and customer record;
- pending-review, accepted, rejected, cancelled and fulfilled states.

A separate storefront-product record can control publication, description,
image, minimum/maximum quantity, lead time and fulfilment policy without
changing the existing `FinishedGood` master. Existing products should default
to unpublished, preserving live data and current back-office flows.

## Routing accepted demand

```text
Customer order intake
        |
        +--> Made to order --> Pending Production Order
        |                         |
        |                         +--> approval releases materials
        |                         +--> completion records batch/cost
        |                         +--> Sale / receivable / cash
        |
        +--> Distribution Market Stock available
        |                         |
        |                         +--> FEFO lot allocation
        |                         +--> Distribution Sale
        |
        +--> Physical stock available
                                  |
                                  +--> reservation / fulfilment
                                  +--> Sale and stock deduction
```

The acceptance policy is tenant and vertical aware:

- Bakery orders can route to scheduled Online/Distribution production or
  available stock.
- Restaurant orders retain dine-in, takeaway/delivery and table/service
  references; catering/bulk demand uses the stable Distribution channel.
- General production can use pending review, quotation and lead-time policies.
- Wholesale routes accepted demand against procured warehouse stock using the
  stable Distribution channel, preserving trade-customer pricing, credit terms,
  receivables and payments without invoking Production.
- Retail routes accepted demand against procured shop stock as an immediate
  Physical Store/POS sale, with supplier arrivals traced through received POs.
- Established wholesale customers can retain customer-specific prices and
  terms; ad-hoc online customers can remain snapshots until deliberately added
  to the customer master.

Payment-provider confirmation should flow into the existing payment and finance
models. A browser redirect is not proof of payment. Payment and fulfilment must
remain independent states.

## Distribution Market Stock integration

`FinishedGood.stock` represents Physical Store stock and cannot safely answer
public Distribution availability. INPROFIC therefore maintains separate,
batch-aware Distribution Market Stock lots.

New unassigned Distribution production flows as follows:

```text
Market-stock Production Order
        --> approval / material release
        --> completion / ProductionBatch / frozen cost / expiry
        --> Distribution Market Stock lot
```

The lot retains batch, quantity, expiry date and frozen unit cost. Customer
release uses first-expiry-first-out allocation and creates an ordinary
Distribution `Sale`, so existing sales, receivable, payment and profitability
reporting continue to work.

Redistributable customer returns become new Market Stock lots while preserving
the original production batch and expiry date. Damaged returns are unsellable
and reported at frozen unit cost. Expired quantities are automatically excluded
from allocation and Physical Store transfer; reconciliation closes the lot and
records the non-cash inventory-loss value.

Unsold Market Stock can be transferred explicitly into Physical Store stock.
For a product normally unavailable in the Physical Store, the transferred
quantity is a narrow shelf-sale allowance. It does not silently change the
product's normal shelf configuration, and shelf sales consume that allowance.

Existing planned offcut, additional excess, production wastage and shortage
reconciliation retain their established semantics. They are not silently
merged into the Market Stock balance. The sole exception is additional excess
on an unassigned Distribution market-stock order: its completion form requires
that amount to be allocated explicitly to Market Stock (or to a stated
non-stock purpose), never directly to the Physical Store shelf.

## Tenant routing without subdomains

Subdomains and custom domains are not required. Back-office requests use the
authenticated user's active `UserBusiness` membership. All businesses can use
one login host and the same internal paths.

Anonymous storefront requests do not have a staff membership session, so their
tenant must be explicit in the public path:

```text
https://app.example.com/shop/blue-kitchen/
https://app.example.com/shop/jordan-bakery/
```

The globally unique `Business.slug` identifies the tenant. API credentials and
webhook configurations must also resolve to exactly one Business; a caller must
never be allowed to submit an arbitrary `business_id`.

Optional subdomains or custom domains can later be aliases:

```text
blue-kitchen.app.example.com
orders.bluekitchen.com
```

A future business-domain mapping can translate either hostname to the same
tenant/storefront. DNS, TLS and allowed-host configuration are deployment
concerns; they do not require separate applications or databases.

## SQLite and PythonAnywhere rollout

The initial design remains a Django monolith using SQLite:

- use short `transaction.atomic()` operations;
- enforce idempotency with unique constraints;
- do not make external HTTP calls while a database transaction is open;
- store inbound/outbound webhook attempts for retry and audit;
- use a PythonAnywhere scheduled management command for retries if required;
- apply rate limits and signed webhook verification;
- release behind a tenant/module preference and default storefront products to
  unpublished.

There is no fixed application-level tenant count. Practical capacity is bounded
by concurrent SQLite writes, database/media size, traffic, reports and hosting
worker limits. The domain and API contracts should remain stable if operational
scale later requires PostgreSQL.

## Safe implementation order

1. Extract reusable commerce/order services from form views.
2. Add commerce intake and storefront-product models through additive
   migrations.
3. Keep storefronts disabled and products unpublished by default.
4. Pilot hosted ordering with pending staff review for one tenant.
5. Add physical-stock reservation before promising stock fulfilment publicly.
6. Add the embeddable component and versioned API.
7. Add provider connectors and payment integrations incrementally.

The safest first public release is **hosted order -> pending review -> existing
production or Market Stock flow**. Existing bakery, restaurant and general
back-office behavior remains unchanged until a tenant enables the new channel.

## Implemented commerce boundary (September 2026)

INPROFIC now implements the API-first version of this plan while retaining the hosted storefront and simple **Order Now** link as alternative entry points.

### Commerce is a subscription module

`commerce` is a normal module in `RoleModulePermission.MODULE_CHOICES` and therefore passes through the same three-layer access stack:

1. `BusinessModuleAccess` commercial entitlement;
2. role permission;
3. optional per-user override.

An explicit `BusinessModuleAccess.enabled=False` remains the hard ceiling. The seeded plan matrix enables Commerce only for BUSINESS PRO. Existing pre-subscription businesses receive an explicit disabled Commerce entitlement during migration, so deploying the public routes does not publish a live tenant accidentally.

### Public product publication

`commerce.StorefrontProduct` is a publication/configuration layer around the existing `inventory.FinishedGood`. It does not duplicate stock. Products are unpublished by default and can independently offer Physical Store/direct, Online and Distribution/bulk order modes. Each mode exposes its INPROFIC-resolved channel price and minimum quantity. Distribution/bulk has a dedicated per-product minimum. Product images are uploaded to tenant-partitioned media storage; the catalog API exposes the uploaded file as an absolute `image_url`. The former URL field remains a fallback for pre-existing live records, but is no longer editable in the publishing form.

For production-centric services, `FinishedGood.physical_saleable_stock` is the immediate storefront availability. Distribution Market Stock remains a separate pool. The existing explicit Market Stock → Physical Store transfer updates the same FinishedGood shelf balance/transfer allowance, so the storefront/API sees the new availability automatically without a commerce-specific stock sync.

### Sales channel versus fulfilment route

The website's commercial choice is preserved on `CommerceIntake.sales_channel` as `physical_store`, `online`, or `distribution`. `CommerceIntake.ordering_mode` remains the internal fulfilment route and is derived by INPROFIC:

- production businesses: Physical Store/direct uses available stock, while Online and Distribution/bulk create made-to-order Production demand after staff acceptance;
- wholesale and retail businesses: all three channel prices use procured finished stock and never invoke Production.

Because those channel choices already determine fulfilment, the publishing form does not expose a separate made-to-order checkbox. For production businesses, enabling Online or Distribution/bulk is sufficient to make that channel a pre-order production route.

Uploaded images require persistent media storage in deployment. `MEDIA_ROOT` defaults to the repository's `media/` directory and can be overridden with the `MEDIA_ROOT` environment variable; `MEDIA_URL` defaults to `/media/`. The production host must map that public URL to the persistent media directory. Django serves it automatically only while `DEBUG=True`.

Older integrations may still send `ordering_mode: stock|preorder`; those map to Physical Store/direct and Online. New callers use `order_mode`.

The customer choice is not inferred from low stock. For an ordinary Order with insufficient stock, the tenant's `CommerceSettings.insufficient_stock_policy` is one of:

1. **reduce** — accept only the quantity currently available;
2. **reject** — reject the request without mutating stock/production;
3. **invite_preorder** — keep the intake waiting for the customer to switch the request to Pre-order;
4. **split** — fulfil the available shelf quantity and create a Pending Online Production Order for the balance.

Stock is rechecked under transaction lock when an intake is accepted. Pending intake does not reserve or deduct inventory.

### API contract

The current versioned write boundary is:

```text
GET  /api/v1/storefronts/{business_slug}/products
POST /api/v1/storefronts/{business_slug}/checkouts
GET  /api/v1/storefronts/{business_slug}/checkouts/{checkout_uuid}
POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_uuid}/payments
GET  /api/v1/storefronts/{business_slug}/checkouts/{checkout_uuid}/payments/current
GET  /api/v1/storefronts/{business_slug}/orders/{public_uuid}   # after verified payment materializes it
```

Product GET is public when Commerce/API are enabled. Checkout/payment calls use a tenant-bound `CommerceIntegration` API key supplied as `X-INPROFIC-Key` and idempotency keys. Duplicate retries return the same checkout/payment. `POST /orders` no longer creates an intake and returns HTTP 410 so a new integration cannot bypass payment-first materialization.

Clients should move to the INPROFIC header names shown here. The server still
accepts the former branded key/signature headers so existing integrations do not
fail during the rename.

Submitted totals are ignored. INPROFIC resolves the selected channel price itself and snapshots it into `CommerceIntakeItem`.

### Hosted storefront and Order Now

A tenant with no website can enable:

```text
/shop/{business_slug}/
```

A business with a simple existing site can use the same URL as its **Order Now** destination. Customers can place several products in one basket. The hosted route validates that basket into a short-lived `CommerceCheckoutSession`, shows only payment methods that are enabled and fully configured for the tenant, and creates no Commerce Intake, Sale, or Production record while payment is pending. Verified full payment materializes exactly one intake; browser returns never confirm payment. The captured customer name is retained on the checkout/intake snapshot and in the commerce audit entry.

### Payment state

Commerce payment state is separate from order and fulfilment state. Public/headless checkout exposes only gateway-confirmed Paystack, Monnify and instant bank transfer. Instant transfer is issued and verified automatically by the tenant-selected Paystack/Monnify provider; manual transfer claims are historical-only. Cash and card-on-terminal are confined to authenticated in-premise staff with supplemental Storefront POS access; cash requires an explicit physical-receipt guard, while Paystack Terminal card settlement still requires provider verification. A verified payment posts one cash-ledger entry and generates an immutable customer receipt; reversals retain the original receipt and use compensating finance records.

## Headless API versus platform webhook / connector

These are different integration directions that converge on the same Commerce Intake:

- **Headless API**: a website/app controlled by the tenant actively calls INPROFIC. It fetches the INPROFIC catalogue, creates a pre-intake checkout with the generated API key, completes payment, and then tracks the resulting order. This is the preferred route for a custom business website.
- **Platform webhook / connector**: an external commerce platform or adapter pushes events into INPROFIC after an order occurs there. The connector sends INPROFIC's normalized order payload to `/api/v1/connectors/{business_slug}/{integration_id}/orders` and signs the raw request body with HMAC-SHA256 using the generated webhook secret in `X-INPROFIC-Signature`.

The connector boundary is intentionally normalized rather than embedding Shopify/WooCommerce-specific payloads into INPROFIC's core service. Provider-specific adapters translate their payload into this contract. Connectors now create the same payment-first `CommerceCheckoutSession` and return its payment endpoint; they no longer create `CommerceIntake` directly. Server-side pricing, tenant policy, reservations, idempotency and customer-attributed audit behavior remain authoritative.

## Independent commerce switches

Commerce Settings exposes independent toggles for:

- Commerce master switch;
- Hosted storefront;
- Order Now link;
- Headless API;
- Platform webhook / connector.

The BusinessModuleAccess Commerce entitlement remains the commercial hard ceiling above all of these switches. Turning on a surface never bypasses a disabled Commerce module. Integration credentials are only created for a surface that has been enabled.

## Commerce master switch

`CommerceSettings.enabled` is the global runtime gate for a tenant's commerce surfaces. Hosted Storefront, Order Now, Headless API and Platform Connector toggles retain their individual configuration while the master switch is off, but public access/order intake through all of those surfaces is blocked. Re-enabling the master switch restores only the individually enabled surfaces; it does not change their saved toggles.

## Exact website integration flow

Keep `X-INPROFIC-Key` in the website server environment; never expose it in
browser JavaScript. The website displays INPROFIC data but does not decide
authoritative prices, totals, payment success, or fulfilment routing.

### 1. Fetch products and render order modes

```http
GET /api/v1/storefronts/{business_slug}/products
```

Each product includes an `order_modes` array. Render its objects directly:

```json
{
  "code": "distribution",
  "label": "Distribution Order",
  "price": "2200.00",
  "min_quantity": "20.00",
  "max_quantity": null,
  "fulfilment_mode": "preorder",
  "available_now": null,
  "lead_time": "24 hours"
}
```

The three possible codes are `physical_store`, `online`, and `distribution`.
Use the supplied vertical-specific `label`. Display the supplied `price`,
minimum, maximum, availability and lead time. Do not infer whether Production
is used; the returned `fulfilment_mode` is authoritative.

### 2. Create a pre-intake checkout from the website server

```http
POST /api/v1/storefronts/{business_slug}/checkouts
X-INPROFIC-Key: <server-side credential>
Idempotency-Key: <one stable UUID per basket submission>
Content-Type: application/json
```

Submit `order_mode`, customer details and product UUID/quantity rows only. Do not send price, amount, fulfilment mode or Production identifiers. INPROFIC snapshots authoritative pricing and, where appropriate, reserves stock. No `CommerceIntake`, Sale, Production Order or finance entry exists yet.

`POST /api/v1/storefronts/{business_slug}/orders` is retired and returns HTTP 410 with `checkout_first_required`.

### 3. Discover and initialize a public payment

```http
GET  /api/v1/storefronts/{business_slug}/payment-methods
POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments
X-INPROFIC-Key: <server-side credential>
Idempotency-Key: <one stable UUID per payment attempt>
```

Public/headless methods are only `paystack`, `monnify`, and `bank_transfer`, and only when fully configured. Never send an amount. Cash and `pos_card` are authenticated in-premise staff methods and are never exposed here.

For hosted Paystack/Monnify checkout, supply an absolute HTTPS `return_url` and customer email when required, then redirect only to the provider URL returned by INPROFIC. A browser return never marks payment paid.

For `bank_transfer`, INPROFIC asks the tenant-selected provider (Paystack or Monnify) for a temporary account for the exact checkout. Display the returned account/expiry and poll. Do not collect a manual transfer reference; signed provider events plus an independent provider verification settle the payment.

### 4. Poll until verified payment materializes the order

```http
GET /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments/current
GET /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}
X-INPROFIC-Key: <server-side credential>
```

Only after full verified settlement does the checkout return an `order_id`. From that point, track the materialized order with `/orders/{order_id}`. A verified payment also exposes an unguessable customer `receipt_path`.

Gateway webhook URLs point directly to INPROFIC, not the website:

```text
/api/v1/storefronts/{business_slug}/payments/paystack/webhook
/api/v1/storefronts/{business_slug}/payments/monnify/webhook
```


### Delivery rider and storefront-customer boundaries

Delivery Rider is a purpose-specific staff access surface: rider-only users can see and act only on assignments linked to their own active in-house driver record. Delivery alerts reuse the durable Commerce notification transport but support targeted rider recipients. Public storefront customers remain guests by default; optional `StorefrontCustomer` profiles are tenant-scoped, separate from staff users, and store purchase history without making registration a checkout requirement. Hybrid delivery means in-house and the configured provider remain interchangeable per order under the tenant’s routing and customer-visible post-payment switch policy.
