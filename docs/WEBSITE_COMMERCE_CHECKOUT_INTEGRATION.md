# INPROFIC Website Commerce Checkout Integration

This is the complete contract for connecting a business-owned website to INPROFIC’s headless Commerce API.

The required architecture is payment-first:

```text
website basket
  -> INPROFIC checkout (validated, priced, optionally reserved)
  -> INPROFIC payment
  -> verified full settlement
  -> exactly one INPROFIC commerce order/intake
  -> operational acceptance, fulfilment, Sales/Production and Finance
```

Do not use the retired `/orders` create endpoint. It is retired for writes and returns HTTP `410 checkout_first_required`. All new and migrated integrations must create `/checkouts`; no operational intake exists until verified payment succeeds.

## 1. Responsibilities and security

INPROFIC is authoritative for published products, channel labels and availability, quantity limits, channel prices, reservations, payable totals, payment eligibility, gateway verification and the resulting commerce order.

The website owns its catalogue presentation, basket, customer-facing history and server-side INPROFIC client. Never send a price or amount from the browser. Never expose `X-INPROFIC-Key` in browser JavaScript, source control, analytics or a public environment variable. Call authenticated endpoints from the website server. Product image URLs are public and may be rendered directly by the browser.

## 2. Configure INPROFIC

### 2.1 Access and public address

Confirm the business has Commerce access under **Plan & Billing**.

Open **Business Settings** and edit **Public business address** if needed. It does not need dash-separated words. These are valid:

```text
yourstore
your_store
your-store
```

The value is `{business_slug}` in all public URLs:

```text
https://INPROFIC_HOST/shop/{business_slug}/
https://INPROFIC_HOST/api/v1/storefronts/{business_slug}/...
```

It must be unique. Changing it preserves all tenant data, but old storefront, API and webhook URLs stop resolving. Update every connected system immediately.

### 2.2 Commerce switches and products

Open **Commerce → Commerce settings** and enable:

1. **Commerce master switch**;
2. **Headless API**;
3. the desired reservation duration and insufficient-stock policy.

Hosted storefront, Order Now and connector switches are independent and are not required by a headless website.

From **Commerce**, configure every product:

- publish it;
- set public name and description;
- upload its image;
- enable applicable ordering channels;
- set each channel minimum and the optional maximum;
- set production lead time where applicable;
- verify its INPROFIC channel prices.

The API returns the uploaded image as an absolute URL in both `image` and the compatibility alias `image_url`.

On PythonAnywhere, media is not deployed by `collectstatic`. Add a Web-tab static-files mapping:

```text
URL:       /media/
Directory: /home/YOUR_USERNAME/PATH_TO_INPROFIC/media
```

`MEDIA_ROOT` must point at the same persistent directory. Reload the web app, then open an API-returned image URL in a private browser window. It must return an image, not a login page or 404.

### 2.3 Payment methods

Create active settlement accounts in **Finance**, then open **Commerce → Payment settings**.

| Method | Required configuration |
| --- | --- |
| Paystack | Enabled, secret key, active tenant settlement account |
| Monnify | Enabled, API key, secret key, contract code, base URL, active tenant settlement account |
| Instant bank transfer | Enabled, Paystack or Monnify selected as transfer provider, that provider fully configured, active tenant transfer settlement account; Monnify also needs the configured transfer bank code |
| Cash / physical POS | **Not exposed to headless/public checkout.** These are authenticated in-premise staff methods only. |

Only fully configured methods are exposed. Credentials remain server-side.

Register gateway webhooks directly against INPROFIC:

```text
POST https://INPROFIC_HOST/api/v1/storefronts/{business_slug}/payments/paystack/webhook
POST https://INPROFIC_HOST/api/v1/storefronts/{business_slug}/payments/monnify/webhook
```

The customer website must not proxy these webhooks. A browser return never confirms payment.

### 2.4 API integration credential

Open **Commerce → Add integration**:

1. enter a recognizable name;
2. select **Headless API**;
3. optionally record the website origin;
4. keep it active and save;
5. copy the generated key into the website server’s secrets.

```dotenv
INPROFIC_BASE_URL=https://your-inprofic-host.example
INPROFIC_BUSINESS_SLUG=yourstore
INPROFIC_API_KEY=replace-with-the-tenant-api-key
```

Do not put a trailing slash on `INPROFIC_BASE_URL`.

## 3. HTTP conventions

Base path:

```text
/api/v1/storefronts/{business_slug}
```

Authenticated requests send:

```http
X-INPROFIC-Key: <tenant API key>
Accept: application/json
```

JSON writes also send `Content-Type: application/json`. Checkout creation and payment initialization each require their own stable header:

```http
Idempotency-Key: <logical-operation-key>
```

Use decimal strings for quantity. Do not calculate financial truth with browser floats.

## 4. Catalogue

```http
GET /api/v1/storefronts/{business_slug}/products
```

This read is available only while the tenant’s Commerce and API switches are enabled.

```json
{
  "business": "Your Business",
  "business_slug": "yourstore",
  "service": "Retail store",
  "products": [
    {
      "id": "7ea4b6b1-3c5a-4de1-9aba-3a7d28d3b810",
      "name": "Everyday Item",
      "description": "A useful product.",
      "image": "https://your-inprofic-host.example/media/commerce/products/business-4/abc123.jpg",
      "image_url": "https://your-inprofic-host.example/media/commerce/products/business-4/abc123.jpg",
      "unit": "pack",
      "available_now": "18.00",
      "order_modes": [
        {
          "code": "physical_store",
          "label": "Retail / Pickup Order",
          "price": "1000.00",
          "min_quantity": "1.00",
          "max_quantity": null,
          "fulfilment_mode": "stock",
          "available_now": "18.00",
          "lead_time": ""
        },
        {
          "code": "distribution",
          "label": "Bulk Customer Order",
          "price": "875.00",
          "min_quantity": "20.00",
          "max_quantity": null,
          "fulfilment_mode": "stock",
          "available_now": "18.00",
          "lead_time": ""
        }
      ]
    }
  ]
}
```

No image produces empty `image` and `image_url` strings. Use `image` in new code.

The stable mode codes are `physical_store`, `online` and `distribution`. Display the returned vertical-specific `label`. Production services normally use `preorder` fulfilment for online/distribution; wholesale and retail remain stock-based and are never forced through production.

The hosted catalogue hides product counts. A headless website may similarly use `available_now` only for validation/UI disabling. INPROFIC always rechecks it during checkout.

Older top-level fields such as `ordering_modes`, `stock_price` and `preorder_price` remain for compatibility. New code should use `order_modes`.

## 5. Payment discovery

```http
GET /api/v1/storefronts/{business_slug}/payment-methods
X-INPROFIC-Key: <tenant API key>
```

```json
{
  "currency": "NGN",
  "methods": [
    {"code": "paystack", "label": "Card / secure checkout (Paystack)"},
    {"code": "monnify", "label": "Secure checkout (Monnify)"},
    {"code": "bank_transfer", "label": "Instant bank transfer (Monnify)"}
  ]
}
```

Public/headless codes are `paystack`, `monnify` and `bank_transfer`. **Cash and `pos_card` are never returned on this surface.** Render only what is returned. INPROFIC revalidates eligibility when payment starts.

## 6. Create checkout

```http
POST /api/v1/storefronts/{business_slug}/checkouts
X-INPROFIC-Key: <tenant API key>
Idempotency-Key: checkout_<website-cart-id>
Content-Type: application/json
```

```json
{
  "external_order_id": "WEB-8821",
  "order_mode": "online",
  "customer": {
    "name": "Ada Customer",
    "phone": "+2348000000000",
    "email": "",
    "address": "12 Example Street"
  },
  "service_mode": "delivery",
  "table_reference": "",
  "items": [
    {"product_id": "7ea4b6b1-3c5a-4de1-9aba-3a7d28d3b810", "quantity": "2"}
  ]
}
```

Rules:

- customer name and phone are required;
- email is optional here;
- product IDs must be published tenant product UUIDs;
- a product may occur only once;
- all items must support one selected mode;
- never send price, amount or total.

INPROFIC records the customer name in the tenant audit trail, snapshots authoritative pricing and creates no Intake, Sale or Production record yet.

HTTP `201` means created; `200` means an idempotent retry returned the existing checkout:

```json
{
  "checkout_id": "649948bd-613f-4d72-821e-7d99ae066d52",
  "status": "awaiting_payment",
  "order_mode": "online",
  "fulfilment_mode": "stock",
  "amount": "2000.00",
  "currency": "NGN",
  "reservation_expires_at": "2026-09-08T15:45:00+01:00",
  "payment_status": null,
  "order_id": null,
  "order_number": null,
  "order": null,
  "materialization_error": "",
  "items": [
    {
      "product_id": "7ea4b6b1-3c5a-4de1-9aba-3a7d28d3b810",
      "name": "Everyday Item",
      "requested_quantity": "2",
      "payable_quantity": "2",
      "reserved_stock_quantity": "2",
      "production_quantity": "0",
      "unit_price": "1000.00",
      "line_total": "2000.00"
    }
  ],
  "created": true,
  "payment_methods_url": "/api/v1/storefronts/yourstore/payment-methods",
  "payment_url": "/api/v1/storefronts/yourstore/checkouts/649948bd-613f-4d72-821e-7d99ae066d52/payments"
}
```

Save `checkout_id`, amount/currency, external ID and checkout idempotency key in the website database/session before payment.

## 7. Checkout errors

General HTTP `400`:

```json
{"detail": "Customer phone number is required."}
```

Minimum HTTP `400`:

```json
{
  "detail": "Everyday Item requires at least 20.00 pack for Bulk Customer Order pricing.",
  "code": "minimum_not_met",
  "suggested_order_modes": [
    {"code": "physical_store", "label": "Retail / Pickup Order"},
    {"code": "online", "label": "Online Order"}
  ]
}
```

Ask the customer before changing mode, then create a new checkout/key.

Availability HTTP `409`:

```json
{
  "detail": "Insufficient stock. The customer may switch to a Pre-order mode before paying.",
  "code": "insufficient_stock",
  "suggested_order_modes": [{"code": "online", "label": "Online Order"}],
  "items": [{
    "product_id": "...",
    "product": "Everyday Item",
    "requested": "20",
    "available_now": "8.00",
    "shortfall": "12.00"
  }]
}
```

Tenant policy may reduce to available stock, reject, invite preorder, or split stock/production where applicable. Always use successful checkout quantities and amount.

Other codes: `403` for invalid/disabled credential; `404` for unavailable tenant-scoped resources; `400` for malformed input, expired checkout or payment validation.

## 8. Initialize payment

```http
POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments
X-INPROFIC-Key: <tenant API key>
Idempotency-Key: payment_<website-attempt-id>
Content-Type: application/json
```

Never send an amount.

### Paystack/Monnify

When checkout email is empty, supply it now because the provider requires one:

```json
{
  "method": "paystack",
  "customer_email": "ada@example.com",
  "return_url": "https://your-website.example/checkout/return?checkout=649948bd-613f-4d72-821e-7d99ae066d52"
}
```

If checkout already has email, omit `customer_email`. `return_url` must be absolute HTTP/HTTPS; use HTTPS in production.

```json
{
  "payment_id": "1119b472-fb63-4a38-8727-eb45cf5d98b1",
  "method": "paystack",
  "status": "awaiting_customer",
  "amount": "2000.00",
  "currency": "NGN",
  "reference": "STP-...",
  "gateway_reference": "STP-...",
  "authorization_url": "https://checkout.paystack.com/...",
  "instructions": "",
  "bank_account": null,
  "expires_at": "2026-09-08T15:45:00+01:00",
  "amount_paid": "0.00",
  "balance": "2000.00",
  "verified_at": null,
  "settled_at": null,
  "checkout_id": "649948bd-613f-4d72-821e-7d99ae066d52",
  "order_id": null,
  "claim": null,
  "checkout": {}
}
```

The real `checkout` is the complete checkout serialization. Redirect to `authorization_url`. On return, display “Confirming payment” and poll. Never trust redirect query parameters.

### Instant bank transfer

```json
{"method": "bank_transfer", "customer_email": "ada@example.com"}
```

INPROFIC uses the tenant's configured transfer provider (`paystack` or `monnify`) to issue a temporary account for the exact checkout amount. The response contains `gateway_provider`, `bank_account`, expiry/instructions and the normal payment reference. Show those details exactly; do **not** ask the customer to submit a transfer reference.

The provider webhook is signature-checked and INPROFIC independently queries the provider before settlement. The intake/order is materialized only after the verified amount, currency, tenant/payment metadata and provider status match. Poll the current-payment endpoint while the transfer is pending.

The historical `/payments/current/claim` endpoint remains only for pre-migration manual-transfer records; gateway-backed new transfers reject manual claims.

### Cash and physical POS

Cash and `pos_card` are deliberately unavailable to public/headless clients. Cash is accepted only by authenticated staff with the supplemental **Commerce storefront access** capability on the in-premise POS screen. Physical card-terminal automation currently uses a configured Paystack Terminal and settles only after provider verification.

## 9. Poll before an order exists

Poll every 5–10 seconds while the customer is waiting, then back off:

```http
GET /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments/current
X-INPROFIC-Key: <tenant API key>
```

```json
{
  "checkout": {
    "checkout_id": "...",
    "status": "awaiting_payment",
    "payment_status": "awaiting_customer",
    "order_id": null,
    "order_number": null,
    "order": null
  },
  "payment": {
    "payment_id": "...",
    "status": "awaiting_customer",
    "amount": "2000.00",
    "amount_paid": "0.00",
    "balance": "2000.00"
  }
}
```

Checkout-only status:

```http
GET /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}
X-INPROFIC-Key: <tenant API key>
```

| Checkout status | Action |
| --- | --- |
| `awaiting_payment` | Continue applicable instructions/polling. |
| `paid` | Verified; materialization in progress. Keep polling and never charge again. |
| `materialized` | Save order UUID/number and switch to order tracking. |
| `paid_review` | Money verified but safe materialization needs staff. Never charge again. |
| `expired` | Unpaid hold ended; create a fresh checkout/key. |
| `cancelled` | Checkout closed. |

Payment statuses are `pending`, `awaiting_customer`, `awaiting_verification`, `partially_paid`, `paid`, `failed`, `cancelled`, and `refunded`.

## 10. Checkout-to-order transition

After verified full payment:

```json
{
  "checkout_id": "649948bd-613f-4d72-821e-7d99ae066d52",
  "status": "materialized",
  "payment_status": "paid",
  "order_id": "e5ef6a60-bdb1-40a4-b2e3-d70a98147fb8",
  "order_number": "WEB-000124",
  "order": {
    "id": "e5ef6a60-bdb1-40a4-b2e3-d70a98147fb8",
    "number": "WEB-000124",
    "status": "pending",
    "payment_state": "confirmed",
    "fulfilment_state": "pending"
  }
}
```

`order_id` is the UUID for API URLs. `order_number` is the tenant-local display reference. Keep them separate.

```http
GET /api/v1/storefronts/{business_slug}/orders/{order_id}
X-INPROFIC-Key: <tenant API key>
```

This response separates `status`, `payment_state`/`payment`, and `fulfilment_state`, plus item requested/stock/production quantities. Do not collapse them into one website status.

## 11. Customer transaction history

Persist checkout history immediately after checkout creation, before gateway redirect:

```json
{
  "customer_or_session_id": "website-owned-id",
  "storetrack_checkout_id": "649948bd-613f-4d72-821e-7d99ae066d52",
  "storetrack_order_id": null,
  "storetrack_order_number": null,
  "external_order_id": "WEB-8821",
  "amount": "2000.00",
  "currency": "NGN",
  "checkout_status": "awaiting_payment",
  "payment_status": null
}
```

Update it after every poll. For anonymous users, use a signed HTTP-only website session and optionally device-local links. Do not expose a phone-only public lookup without OTP; it can leak other customers’ orders.

The hosted INPROFIC catalogue already saves tenant-specific unguessable tracking links in that browser. A headless website owns its own history UI.

## 12. Idempotency and reservations

- Reuse a checkout key only for the same logical basket retry.
- Use a separate stable payment key per logical attempt.
- Use a new payment key when deliberately changing/retrying a failed method.
- Never reuse a key for another basket or payment method.
- Keep `external_order_id` unique for the website order within that tenant/source.

Duplicate retries/callbacks cannot materialize two intakes for one checkout.

Stock fulfilment is reserved at checkout. The configurable hold is 5–120 minutes (default 15). Active holds reduce web and direct/POS availability. Unpaid expiry releases stock. Production/preorder channels preserve production demand instead.

Late verified payment or a safe-materialization failure becomes `paid_review`: financial truth remains, staff can recover without charging again, and the website must show “paid, under review.”

## 13. Server-side reference client

This JavaScript belongs on the website server, not in the browser:

```js
const base = process.env.INPROFIC_BASE_URL;
const slug = process.env.INPROFIC_BUSINESS_SLUG;
const apiKey = process.env.INPROFIC_API_KEY;

async function storeTrack(path, { method = "GET", body, idempotencyKey } = {}) {
  const response = await fetch(`${base}/api/v1/storefronts/${slug}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      "X-INPROFIC-Key": apiKey,
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {})
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store"
  });
  const payload = await response.json().catch(() => ({ detail: "Unreadable INPROFIC response." }));
  if (!response.ok) {
    const error = new Error(payload.detail || `INPROFIC returned ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

export const createCheckout = (input, key) =>
  storeTrack("/checkouts", { method: "POST", body: input, idempotencyKey: key });

export const startPayment = (checkoutId, input, key) =>
  storeTrack(`/checkouts/${checkoutId}/payments`, { method: "POST", body: input, idempotencyKey: key });

export const checkoutStatus = checkoutId =>
  storeTrack(`/checkouts/${checkoutId}/payments/current`);

export const orderStatus = orderId => storeTrack(`/orders/${orderId}`);
```

The browser calls the website’s own API routes; the website server attaches the secret.

## 14. Go-live checklist

- [ ] INPROFIC uses HTTPS and correct `ALLOWED_HOSTS`.
- [ ] `MEDIA_ROOT` is persistent and `/media/` is mapped on PythonAnywhere.
- [ ] Commerce module, master switch and Headless API are enabled.
- [ ] Final business slug matches website and gateway configuration.
- [ ] Published products return absolute working `image` URLs.
- [ ] All three applicable channel codes, labels, prices and limits are tested.
- [ ] API key exists only in website server secrets.
- [ ] Only configured payment methods appear.
- [ ] Gateway webhooks point directly to INPROFIC.
- [ ] Checkout requires name/phone; email is optional until a gateway requires it.
- [ ] Website stores checkout history before redirect.
- [ ] Browser return starts polling and never confirms payment.
- [ ] `paid_review` prevents repeat charging.
- [ ] Order UUID and display number are stored separately.
- [ ] Duplicate requests and cross-tenant UUID/key attempts are tested.
- [ ] Public/headless methods contain no cash/POS option.
- [ ] Instant bank transfer displays the provider-issued temporary account and settles only after webhook + provider verification.
- [ ] Cash and physical terminal payments are tested only from the authenticated in-premise Storefront POS.

## 15. Earlier integration compatibility

`POST /api/v1/storefronts/{business_slug}/orders` is now **retired for writes**. It returns HTTP `410` with `code: "checkout_first_required"` and points the caller to `/checkouts`. This prevents any new integration from materializing an intake before payment.

Read/status and payment routes for already-existing historical intake UUIDs remain available during migration:

```text
GET  /api/v1/storefronts/{business_slug}/orders/{order_id}
POST /api/v1/storefronts/{business_slug}/orders/{order_id}/payments/initiate
GET  /api/v1/storefronts/{business_slug}/orders/{order_id}/payments/current
POST /api/v1/storefronts/{business_slug}/orders/{order_id}/payments/current/claim   # historical manual transfers only
```

For every new or migrated website: create `/checkouts`, pay using the checkout UUID, poll until verified settlement returns an `order_id`, then begin order tracking.


## Delivery quotes and category grouping

Product API responses include the business-defined category where present, so
external storefronts should group products by the supplied category value rather
than hardcoding menu groups. Made-in-house and purchased-for-resale remain source
properties, not category replacements.

For delivery orders, request or render the INPROFIC delivery quote before
initializing payment. The quoted delivery fee must be included in the checkout
amount. A paid checkout can then materialize one order and one delivery
assignment. The delivery assignment may remain in-house, hybrid/manual, or be
dispatched through the configured Glovo LaaS v2 plug-in provider when the tenant enables
a provider account.


### Delivery rider and storefront-customer boundaries

Delivery Rider is a purpose-specific staff access surface: rider-only users can see and act only on assignments linked to their own active in-house driver record. Delivery alerts reuse the durable Commerce notification transport but support targeted rider recipients. Public storefront customers remain guests by default; optional `StorefrontCustomer` profiles are tenant-scoped, separate from staff users, and store purchase history without making registration a checkout requirement. Hybrid delivery means in-house and the configured provider remain interchangeable per order under the tenant’s routing and customer-visible post-payment switch policy.
