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
payment verification. The delivery engine then owns in-house, hybrid and external-partner delivery without allowing the storefront to create operational delivery records directly. External partners are provider-neutral by default: INPROFIC remains authoritative for the customer quote, routing choice, assignment and timeline, while a configured partner account can stay manual or optionally receive dispatches through the INPROFIC Delivery Adapter v1 contract. Glovo LaaS v2 remains an optional built-in adapter only when the Founder has exposed it platform-wide and the business has configured its own approved account.

Named delivery areas are geometry-backed rather than tariff-only labels. Each
area has a mapped centre, radius and optional diagonal extensions. That geometry
decides whether an address/pin is serviceable; the linked pricing band supplies
base/per-kilometre fee, minimum basket and ETA, while the billable distance is
measured from the active delivery base to the precise validated destination.
Hosted storefront, POS and Headless API use the same quote service. Address text
is geocoded server-side first; if it cannot be located inside the selected area,
the customer is prompted to correct it or place an exact map pin, which is still
checked against the same coverage boundary. Headless clients receive a
`coverage_polygon` so their maps can display INPROFIC's configured shape without
reimplementing the radius/diagonal algorithm.

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

`commerce.StorefrontProduct` is a publication/configuration layer around the existing `inventory.FinishedGood`. It does not duplicate stock. Products are unpublished by default and can independently configure Physical Store/direct, Online and Distribution/bulk channels. Physical Store/direct is consumed only by the staff-operated in-premise POS. Hosted storefronts, connectors and the headless API publish only Online and Distribution/bulk. Distribution/bulk has a dedicated per-product minimum and is available on both POS and external surfaces when enabled. Product images are uploaded to tenant-partitioned media storage; the catalog API exposes the uploaded file as an absolute `image_url`. The former URL field remains a fallback for pre-existing live records, but is no longer editable in the publishing form.

For production-centric services, `FinishedGood.physical_saleable_stock` is the immediate storefront availability. Distribution Market Stock remains a separate pool. The existing explicit Market Stock → Physical Store transfer updates the same FinishedGood shelf balance/transfer allowance, so the storefront/API sees the new availability automatically without a commerce-specific stock sync.

### Sales channel versus fulfilment route

The persisted commercial choice remains `physical_store`, `online`, or `distribution`, but surface boundaries are enforced before checkout creation: `physical_store` is POS-only; hosted/API/connector sources accept only `online` or `distribution`. `CommerceIntake.ordering_mode` remains the internal fulfilment route and is derived by INPROFIC:

- production businesses: Physical Store/direct uses available stock, while Online and Distribution/bulk create made-to-order Production demand after staff acceptance;
- wholesale and retail businesses: all three channel prices use procured finished stock and never invoke Production.

Because those channel choices already determine fulfilment, the publishing form does not expose a separate made-to-order checkbox. For production businesses, enabling Online or Distribution/bulk is sufficient to make that channel a pre-order production route.

Uploaded images require persistent media storage in deployment. `MEDIA_ROOT` defaults to the repository's `media/` directory and can be overridden with the `MEDIA_ROOT` environment variable; `MEDIA_URL` defaults to `/media/`. The production host must map that public URL to the persistent media directory. Django serves it automatically only while `DEBUG=True`.

Older request aliases remain accepted only where they resolve to a channel valid for that surface. External callers should use `order_mode: online|distribution`; any external request that resolves to `physical_store` is rejected rather than silently receiving an in-premise price.

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
POST /api/v1/storefronts/{business_slug}/delivery/location
POST /api/v1/storefronts/{business_slug}/delivery/quote
GET  /api/v1/storefronts/{business_slug}/payment-methods
POST /api/v1/storefronts/{business_slug}/checkouts
GET  /api/v1/storefronts/{business_slug}/checkouts/{checkout_uuid}
POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_uuid}/payments
GET  /api/v1/storefronts/{business_slug}/checkouts/{checkout_uuid}/payments/current
GET  /api/v1/storefronts/{business_slug}/orders/{public_uuid}   # after verified payment materializes it
POST /api/v1/storefronts/{business_slug}/orders/{public_uuid}/preorder
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

Commerce payment state is separate from order and fulfilment state. Public/headless checkout can expose provider-confirmed Paystack and Monnify, provider-issued `bank_transfer`, and the native no-gateway `transfer` method when each is fully configured. `bank_transfer` remains the automatic Paystack/Monnify instant-transfer flow. Native `transfer` displays the tenant's configured static bank details, requires a customer payment-proof upload, and remains `awaiting_verification` until authorized staff confirms the credit. Cash and card-on-terminal are confined to authenticated in-premise staff with supplemental Storefront POS access. In-premise `transfer` is also available to staff, but—as with cash—requires an explicit staff receipt/verification guard and does not ask the customer to upload proof. A verified payment posts one cash-ledger entry and generates an immutable customer receipt; reversals retain the original receipt and use compensating finance records.

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

External product APIs return only `online` and `distribution` in `order_modes`. The in-premise POS additionally uses `physical_store`/the vertical's direct-sale channel. Use the supplied vertical-specific `label`. Display the supplied `price`, minimum, maximum, availability and lead time. Do not infer whether Production
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

Public/headless methods can be `paystack`, `monnify`, `bank_transfer`, and `transfer`, but only when each method is fully configured. Never send an amount. `transfer` is INPROFIC's native no-gateway bank-transfer option; `bank_transfer` remains the gateway-backed temporary-account option. Cash and `pos_card` are authenticated in-premise staff methods and are never exposed here.

For hosted Paystack/Monnify checkout, supply an absolute HTTPS `return_url` and customer email when required, then redirect only to the provider URL returned by INPROFIC. A browser return never marks payment paid.

For `bank_transfer`, INPROFIC asks the tenant-selected provider (Paystack or Monnify) for a temporary account for the exact checkout. Display the returned account/expiry and poll. Do not collect a manual transfer reference; signed provider events plus an independent provider verification settle the payment.

For native `transfer`, initiate with `{"method":"transfer"}`. INPROFIC returns the tenant-configured static `bank_account` and `proof_required: true`. After the customer transfers the exact authoritative checkout amount, submit `payer_name`, `transfer_reference`, and a required `payment_proof` file as `multipart/form-data` to `POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments/current/claim`. Accepted proof types are JPG/JPEG, PNG, WEBP and PDF up to 10 MB. The API exposes only `claim.proof_received`, never a public proof URL. Continue polling while the payment is `awaiting_verification`; only authorized staff verification may settle it and materialize the order.

Inside INPROFIC, an unresolved native Transfer claim is treated as payment activity that needs staff attention. Authorized Commerce staff see it in the shared movable alert tray; if the business enables repeating Commerce sounds, the alert sound continues at the configured interval while the item remains unread. This is an operator-side safeguard only and does not change the headless API contract: external websites should continue polling authoritative payment status and must never assume that an alert or sound means a payment is verified.

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

### Delivery-provider adapter contract

A business can add any external courier under **Delivery → Provider** without making that courier the delivery control engine. The account can remain staff-managed, or it can opt into API automation. Custom providers use INPROFIC rate bands for the customer-facing delivery fee and ETA snapshot; they do not overwrite the accepted checkout fee.

For automatic dispatch, configure the partner account's API base URL, dispatch endpoint and authentication. An optional merchant/store/location identifier is sent as `partner.store_id`. INPROFIC sends JSON with `schema: "inprofic.delivery.v1"` and `event: "dispatch"`, containing the delivery UUID, order number, customer contact, pickup, destination, accepted delivery fee/order total and quote snapshot. The adapter should return an external reference/tracking number and may return `status` plus `tracking_url`. If no tracking URL is returned, INPROFIC can derive it from the account's configured public tracking URL/template using `{external_reference}` or by appending the escaped reference to a configured base URL.

For inbound status synchronization, assign a webhook secret and have the partner call:

```text
POST /api/v1/delivery/providers/custom/{business_slug}/{provider_id}/webhook
Authorization: Bearer <provider webhook secret>
Content-Type: application/json
```

A minimal callback is `{"external_reference":"...","status":"..."}`; `tracking_url` is optional. Tenant-configured `status_mapping` translates partner statuses to INPROFIC delivery states. These callbacks are data-continuity writes: if a subscription later hides the Delivery workspace, INPROFIC can still keep already-created assignments synchronized underneath so an upgrade reveals complete history.

The optional Glovo adapter is different only in adapter implementation, not in delivery ownership. It is **Founder-gated, off by default, and tenant-disabled until fully configured**. Turning the Founder switch off hides Glovo references outside the Founder Console and blocks Glovo execution without deleting saved tenant credentials or history. When enabled, each business must use its own Glovo business account/API access; INPROFIC does not provide or resell Glovo accounts and does not imply official partnership status.


### Delivery rider and storefront-customer boundaries

Delivery Rider is a purpose-specific staff access surface: rider-only users can see and act only on assignments linked to their own active in-house driver record. Delivery alerts reuse the durable Commerce notification transport but support targeted rider recipients. Public storefront customers remain guests by default; optional `StorefrontCustomer` profiles are tenant-scoped, separate from staff users, and store purchase history without making registration a checkout requirement. Hybrid delivery means in-house and the configured provider remain interchangeable per order under the tenant’s routing and customer-visible post-payment switch policy.

## Headless checkout UI contract: complete exposed surface

A headless website should treat INPROFIC as the authoritative commerce and delivery backend while owning its own presentation. The current customer-facing API surface is:

```text
GET  /api/v1/storefronts/{business_slug}/products
POST /api/v1/storefronts/{business_slug}/delivery/location
POST /api/v1/storefronts/{business_slug}/delivery/quote
GET  /api/v1/storefronts/{business_slug}/payment-methods
POST /api/v1/storefronts/{business_slug}/checkouts
GET  /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}
POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments
GET  /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments/current
POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments/current/claim  # native `transfer` proof submission
GET  /api/v1/storefronts/{business_slug}/orders/{public_id}
GET  /api/v1/storefronts/{business_slug}/deliveries/{delivery_id}/tracking
POST /api/v1/storefronts/{business_slug}/orders/{public_id}/preorder
```

Write calls use the tenant-bound `X-INPROFIC-Key` credential. Checkout and payment creation also require the documented idempotency headers. A headless client must never calculate an authoritative total locally: product prices, delivery fees and final checkout totals returned by INPROFIC win over browser calculations.

Because the API key is a server credential, browser JavaScript must **not** call protected INPROFIC write/location endpoints directly. A headless website should proxy those calls through its own backend/serverless route, keep `X-INPROFIC-Key` there, and return only the customer-safe response to the browser. This applies to the live address/map synchronization endpoint as well as checkout/payment creation.

The following older routes are still present for compatibility but are **not** the preferred new-integration path:

```text
POST /api/v1/storefronts/{business_slug}/orders
     # deliberately returns HTTP 410 checkout_first_required
POST /api/v1/storefronts/{business_slug}/orders/{public_id}/payments/initiate
GET  /api/v1/storefronts/{business_slug}/orders/{public_id}/payments/current
POST /api/v1/storefronts/{business_slug}/orders/{public_id}/payments/current/claim
```

New websites should use checkout-scoped payment endpoints. Provider webhooks are server-to-server endpoints handled by INPROFIC, not browser endpoints:

```text
POST /api/v1/storefronts/{business_slug}/payments/{provider}/webhook
POST /api/v1/delivery/providers/custom/{business_slug}/{provider_id}/webhook
POST /api/v1/delivery/providers/glovo/{business_slug}/webhook   # only while the Founder-level Glovo integration switch is enabled
```

The normalized platform-connector ingestion boundary is separate from headless storefront checkout:

```text
POST /api/v1/connectors/{business_slug}/{integration_id}/orders
```

### Product and delivery discovery

`GET /products` returns business identity, product categories, products and the `delivery` discovery object. For each product, `order_modes[]` exposes the channel code/label, unit price, minimum and maximum quantity, fulfilment mode, immediate availability where applicable, and lead-time information. The same response exposes the tenant's delivery areas, radius, diagonal extensions, `coverage_polygon`, pricing metadata, `location_url` and `quote_url`.

A website may use `coverage_polygon` directly with Leaflet, MapLibre, Google Maps or a similar client-side map. It should not attempt to reproduce INPROFIC's diagonal-extension interpolation itself. The polygon is the visual guide; the server remains authoritative for coverage validation.

### Required two-way address and map synchronization

Headless websites should keep the precise-address field and map pin synchronized exactly as INPROFIC's hosted storefront and POS do.
The examples below show the INPROFIC call itself; in a browser-based website, make that call from your own backend proxy so the tenant API key never reaches the browser. Match INPROFIC's hosted/POS interaction: wait for **2,000 ms of continuous input idleness** before sending a typed-address lookup. Every keystroke, deletion, paste or correction resets that timer. If an older lookup is already in flight when the customer edits again, cancel it where possible or discard its response so stale autocomplete results cannot overwrite the corrected address. Your backend then forwards the settled text to INPROFIC and returns the normalized location result.

**Typed address -> map pin**

Call:

```http
POST /api/v1/storefronts/{business_slug}/delivery/location
X-INPROFIC-Key: <tenant api key>
Content-Type: application/json

{
  "area_id": 12,
  "address": "14 Example Street, Victoria Island, Lagos"
}
```

Successful response:

```json
{
  "address": "14 Example Street, Victoria Island, Lagos, Nigeria",
  "latitude": "6.4281000",
  "longitude": "3.4219000",
  "validated_by": "geocoded_address",
  "location_source": "geocoded_address",
  "address_resolved": true,
  "area_id": 12,
  "area_name": "Victoria Island"
}
```

The website should move the pin to those coordinates, zoom to the resolved destination, and retain/update the visible address with the returned normalized address.

**Map pin -> address field**

Call the same endpoint with coordinates instead:

```json
{
  "area_id": 12,
  "latitude": "6.4281000",
  "longitude": "3.4219000",
  "address": "optional current field text"
}
```

INPROFIC reverse-geocodes the pin and returns the corresponding address with `validated_by: "map_pin"`. The website should replace the address field with the returned address when `address_resolved` is true. If reverse geocoding cannot identify a precise street address, keep the existing user text and visibly ask the customer to confirm/refine it rather than inventing an address.

When `area_id` is supplied, both forward and reverse location resolution enforce that area's radius/diagonal coverage. An out-of-range point returns HTTP 400. Display this and other validation failures as clear red error text near the delivery fields.

The location endpoint intentionally does **not** evaluate basket minimums or calculate a delivery fee. This allows address/map synchronization while the customer is still editing the basket. Quote only after the location is valid.

### Delivery quote after location synchronization

Once the website has synchronized the address and coordinates, request the authoritative delivery quote:

```json
{
  "subtotal": "12500.00",
  "area_id": 12,
  "address": "14 Example Street, Victoria Island, Lagos, Nigeria",
  "latitude": "6.4281000",
  "longitude": "3.4219000",
  "location_source": "geocoded_address"
}
```

Use `location_source: "map_pin"` when the customer chose the point directly. INPROFIC revalidates the coverage and then applies the area's linked minimum order, distance pricing, hybrid/provider routing and ETA rules. HTTP 400 errors such as out-of-range destination or minimum-order failure should be shown to the customer as red validation text; the website must not override them locally.

A Hybrid tenant may return `selection_required: true` plus `options[]`. Present those choices and submit the selected option's `quote_id`. Otherwise use the top-level selected `quote_id`. The quote snapshot is what must be passed into checkout; do not recompute the delivery fee after the quote is returned.

### Final pre-payment review contract

A headless checkout should display, at minimum, product name, selected quantity, the INPROFIC unit price for the chosen `order_mode`, line total, products subtotal, delivery fee (when present), and final checkout total. Unit price should remain visible in the review step so the customer can verify how each line was calculated.

For a delivery checkout, the final review immediately before payment must also keep the accepted INPROFIC delivery snapshot visible: `provider_label`, `eta_min_minutes`, `eta_max_minutes`, `distance_km`, `destination.area_name`, `destination.address`, `destination.validated_by`, and `switch_policy_text` when present. Do not collapse those details after the customer accepts a quote. The customer should be able to see both what is being bought and how the order is expected to reach them before money is collected.

Checkout creation and `GET /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}` return the accepted snapshot again under `delivery`. This is the preferred source for the final review because it is attached to the checkout itself; a headless site does not need to rely only on a browser-cached copy of the earlier quote response. The `delivery` object contains the accepted quote ID, provider/method label, distance, delivery fee, ETA range, precise destination and validation source, plus the Hybrid routing/switch-policy snapshot when applicable.

If `delivery` is `null`, the checkout is not a delivery checkout. Never synthesize an ETA, provider name, distance or switch policy that INPROFIC did not return.

### Payment, receipt and live delivery tracking

Create the checkout only after basket/channel/delivery validation. Use the payment-method discovery endpoint to show only methods INPROFIC considers available for that tenant/surface. Payment initiation returns the provider flow/status; poll the current-payment endpoint or follow the documented provider redirect/webhook path. A paid checkout eventually exposes the materialized order and receipt/tracking information through the checkout/order responses. Browser redirects are never proof of settlement.

For delivery orders, INPROFIC exposes an authoritative customer-safe tracking snapshot:

```text
GET /api/v1/storefronts/{business_slug}/deliveries/{delivery_id}/tracking
X-INPROFIC-Key: <tenant api key>
```

The response contains `order`, `delivery`, `timeline` and `realtime`. The timeline begins with the paid/materialized order confirmation and then contains delivery events. The delivery object includes provider/driver display data, `picked_up_at`, `eta_min_at`, `eta_max_at`, delivered time and the current status. Before pickup, the ETA fields are `null`: INPROFIC deliberately does not count courier travel time from order confirmation. On the first `picked_up`/`out_for_delivery` transition, the ETA window is anchored to that pickup timestamp using the accepted delivery quote's minimum/maximum ETA minutes.

Example shape:

```json
{
  "order": {
    "id": "...",
    "number": "WEB-000123",
    "status": "confirmed",
    "confirmed_at": "2026-09-18T13:02:00Z"
  },
  "delivery": {
    "id": "...",
    "status": "out_for_delivery",
    "status_label": "Out for delivery",
    "provider_label": "In-house delivery",
    "driver": "Rider name",
    "picked_up_at": "2026-09-18T13:24:00Z",
    "eta_min_at": "2026-09-18T13:49:00Z",
    "eta_max_at": "2026-09-18T14:14:00Z",
    "delivered_at": null
  },
  "timeline": [
    {"status": "confirmed", "status_label": "Order confirmed", "created_at": "..."},
    {"status": "picked_up", "status_label": "Picked up", "created_at": "..."},
    {"status": "out_for_delivery", "status_label": "Out for delivery", "created_at": "..."}
  ],
  "realtime": {
    "event_type": "delivery.changed",
    "fallback_poll_seconds": 10
  }
}
```

Hosted INPROFIC tracking uses a customer-safe WebSocket wake-up channel. A `delivery.changed` message is intentionally only a signal that something changed; it does **not** carry customer/order data. The page immediately re-fetches the authoritative status snapshot and redraws status, pickup-based ETA and timeline. Dispatcher, rider and supported provider/webhook status changes publish the same signal, so open Delivery Console, Rider and customer tracking surfaces update without a full page reload.

For a headless website, keep `X-INPROFIC-Key` on the website server. The normal browser-safe pattern is:

1. website browser calls its own `/api/orders/{id}/tracking` route;
2. website backend calls the INPROFIC tracking endpoint with `X-INPROFIC-Key`;
3. browser renders the returned status/ETA/timeline;
4. repeat every roughly 8–10 seconds as a fallback, or have the website backend maintain/proxy the INPROFIC realtime signal and trigger an immediate re-fetch.

Do not expose the API key in browser JavaScript. Also do not treat the realtime wake-up as authoritative state: always re-fetch the tracking snapshot. Direct cross-origin browser WebSockets depend on the deployment's allowed-origin policy, so server-side proxy/SSE/WebSocket relay or polling is the portable headless pattern.

Customer registration is not required for headless integration. A tenant website may keep its own customer profile/session and store INPROFIC checkout/order/delivery UUIDs against that profile. Guest checkout and unguessable hosted tracking remain valid.

## Portion and Bulk-Pack contract

Commerce supports an additive selling/yield layer over Finished Goods. `FinishedGood.unit` remains the private production/stock basis (scoop, piece, ml, bottle, etc.). An optional Standard Portion maps one customer unit such as a plate/serving/set back to that basis; optional Bulk Packs define larger customer-visible choices with their own price and minimum. The checkout snapshots the customer unit/price and the private fulfilment multiplier so later profile edits cannot rewrite historical orders.

Composed products can list additional Finished Goods (including procured-for-resale goods) and raw/packaging materials. This composition metadata does not rewrite recipes or reclassify the component inventory records. Public catalogue payloads expose only customer-safe content names/explicit public quantity labels. The internal base conversion remains private.

Finished/procured goods remain independently publishable through `StorefrontProduct.published`. In addition, one published Finished Good can expose **Individual / plain selling options** backed by that same stock/production balance—for example `Extra Jollof Rice`, `Single Chicken`, `500 ml Juice`, or `Single Chair`. These options create no duplicate recipe or inventory record. Each option has its own customer-facing name/unit, base-unit multiplier, channel availability, channel price and minimum quantity.

The public product response keeps `products[]` for backward compatibility and additionally exposes `catalogue_items[]` as the easiest render-ready list. An individual entry has `kind: "individual_option"`, the parent `product_id`, its own `individual_option_id`, customer-safe `contents`, and only the external channel modes that the business enabled. `physical_store` flags/prices are never serialized to an external client.

For a normal product line submit `product_id` + `quantity`. For an individual/plain line submit the same parent `product_id`, the selected `individual_option_id`, and `quantity`. For a Distribution/Bulk pack submit `bulk_pack_id`; a line cannot combine `individual_option_id` and `bulk_pack_id`. A `bulk_pack_id` is valid only with the Distribution/Bulk channel, while an individual option is valid only on the specific Online or Distribution/Bulk channels enabled for that option. Physical Store/direct pricing remains unavailable to external checkout and product APIs. Clients must render `order_modes[].label` from INPROFIC so vertical wording such as `Catering / Bulk Order` is preserved.

At fulfilment, additional snapshotted composition rows are released from their original inventory class exactly once: Finished/procured components use finished-good stock movements and raw/packaging components use raw-material consumption movements. Fulfilment scopes (`all`, `dine_in`, `takeaway`, `delivery`, `bulk`) decide whether a component applies. Made-to-order base production still uses the existing recipe engine; its additional package/assembly components are released only when that production order completes. The snapshot boundary means later edits to a portion or pack cannot rewrite a paid historical order.
