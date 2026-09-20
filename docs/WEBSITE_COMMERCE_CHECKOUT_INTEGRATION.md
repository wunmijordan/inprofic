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

Product categories are configured separately under **Inventory → Product Categories**.
Create the tenant's customer-facing groups (for example Meals, Drinks,
Accessories or Bulk Packs), keep the categories active, and assign one to each
Finished Good. A category appears in the API only when it is active and has at
least one published Commerce product. Category assignment does not change
whether the product is made in-house or purchased for resale.

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
| Transfer (no gateway) | Enabled, static bank name/account name/account number, instructions if desired, and an active tenant settlement account. Customer proof is required on hosted/headless checkout. |
| Instant bank transfer | Enabled, Paystack or Monnify selected as transfer provider, that provider fully configured, active tenant transfer settlement account; Monnify also needs the configured transfer bank code. This is the gateway-backed `bank_transfer` method. |
| Cash / physical POS | **Not exposed to headless/public checkout.** These are authenticated in-premise staff methods only. Staff-operated POS can also use native `transfer`, with staff confirmation instead of a customer proof upload. |

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

### 2.5 Delivery setup

Delivery is tenant-configured once in INPROFIC and then shared by the hosted
storefront, authenticated in-premise POS and headless website API. A website
must not maintain a separate delivery tariff.

Before publishing delivery, the tenant should complete **Delivery** in this
order:

1. confirm the plan includes Delivery and Commerce;
2. add an active **Delivery / office base**, place its map pin accurately and
   mark the normal pickup base as default;
3. add active **Delivery price bands** with base/per-kilometre fees, minimum
   basket values and customer-facing ETAs. The band's min/max distance fields
   are fallback rules for map-only quotes; named destination areas use their
   own mapped coverage geometry;
4. add active **Delivery destinations / zones**, link each to a price band, place
   the centre pin, then drag the radius handle to define its maximum normal
   coverage. Optional NE/SE/SW/NW handles extend diagonal corridors that a
   circle cannot cover cleanly;
5. add active in-house riders or a configured external delivery-partner account. A partner may remain staff-managed or use the provider-neutral API adapter for automatic dispatch and status callbacks;
6. open **Delivery → Settings**, choose In-house, External provider or Hybrid,
   review its routing/switch policy, then enable delivery.

For a named area, the radius/extensions decide whether the destination is
serviceable. The linked price band supplies the base fee, per-kilometre rate,
minimum order and ETA. The quoted kilometre distance is always measured from
the delivery base to the customer's precise validated address or map pin.

For a custom delivery partner, INPROFIC remains the control engine: customer pricing comes from INPROFIC price bands and the accepted quote remains authoritative. The provider account can be manual or can point to the courier/merchant adapter's API base URL and dispatch endpoint, with optional health-check endpoint, credentials, tracking URL, webhook secret and status mapping. Automatic dispatch uses the `inprofic.delivery.v1` payload documented below, and provider status callbacks return to the account-specific INPROFIC webhook.

Glovo LaaS v2 is an optional built-in adapter only when the Founder enables it platform-wide. It stays inactive until the business completes its own approved Glovo account/API setup (base URL, client credentials, quote/order endpoints, Address Book pickup ID and webhook secret). INPROFIC does not provide or resell Glovo accounts. When the Founder switch is off, Glovo is omitted from tenant/customer/API-facing software surfaces while saved configuration remains dormant for possible later re-enable. Provider and payment webhooks point directly to INPROFIC, not through the customer website.

The Delivery dashboard shows whether the public checkout prerequisites are
ready. Turning Delivery off removes it from new public/POS/API checkouts without
rewriting historical orders or assignments. Existing provider callbacks remain synchronization writes for already-created deliveries, so a plan upgrade can reveal complete status history.

### Custom delivery-provider adapter contract

Custom partner accounts use INPROFIC pricing/routing and can remain entirely manual. When `auto_dispatch` is enabled, INPROFIC POSTs a normalized JSON body to the configured dispatch endpoint:

```json
{
  "schema": "inprofic.delivery.v1",
  "event": "dispatch",
  "partner": {"store_id": "optional-merchant-or-location-id"},
  "delivery": {
    "id": "<delivery uuid>",
    "order_number": "WEB-000123",
    "customer": {"name": "...", "phone": "...", "email": "..."},
    "pickup": {"name": "...", "address": "...", "latitude": 6.4, "longitude": 3.4},
    "destination": {"address": "...", "latitude": 6.5, "longitude": 3.5},
    "amounts": {"currency": "NGN", "delivery_fee": "2500.00", "order_total": "12500.00"},
    "quote": {"id": "<quote uuid>", "distance_km": "7.20", "eta_min_minutes": 20, "eta_max_minutes": 35}
  }
}
```

`partner.store_id` is the optional merchant/store/location identifier configured for that courier account. The adapter may return `external_reference` (or `tracking_number`/`id`), `status`, and `tracking_url`. If it does not return a tracking URL, INPROFIC can build one from the account's configured public tracking URL/template; use `{external_reference}` as the placeholder, or configure a base URL to have the escaped reference appended. For later updates it POSTs to `/api/v1/delivery/providers/custom/{business_slug}/{provider_id}/webhook` with `Authorization: Bearer <webhook secret>` and a minimal body such as `{"external_reference":"partner-123","status":"delivered","tracking_url":"https://..."}`. The account's status mapping converts provider terminology to INPROFIC statuses.

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
The `coverage_polygon` below is shortened for readability; the live response returns
enough ordered boundary points for the website to draw the configured coverage shape.

```json
{
  "business": "Your Business",
  "business_slug": "yourstore",
  "service": "Retail store",
  "categories": [
    {"id": 14, "name": "Everyday Essentials", "slug": "everyday-essentials"}
  ],
  "delivery": {
    "enabled": true,
    "quote_required_before_checkout": true,
    "destination_area_supported": true,
    "destination_address_required": true,
    "destination_coordinates_supported": true,
    "destination_address_validation": "server_geocode_or_map_pin",
    "destination_address_flow": "server_geocode_then_map_pin_fallback",
    "coverage_shape": "circle",
    "coverage_geometry": "radius_with_optional_diagonal_extensions",
    "coverage_geometry_version": 1,
    "quote_url": "/api/v1/storefronts/yourstore/delivery/quote",
    "areas": [
      {
        "id": 8,
        "code": "victoria-island",
        "name": "Victoria Island",
        "latitude": "6.4281000",
        "longitude": "3.4219000",
        "radius_km": "4.50",
        "coverage_shape": "circle_with_diagonal_extensions",
        "diagonal_extensions_km": {"ne": "1.50", "se": "0.00", "sw": "0.00", "nw": "0.75"},
        "coverage_polygon": [
          {"latitude": "6.4686012", "longitude": "3.4219000"},
          {"latitude": "6.4659503", "longitude": "3.4437294"},
          {"latitude": "6.4580421", "longitude": "3.4626450"}
        ],
        "pricing": {
          "band": "Central zone",
          "base_fee": "500.00",
          "per_km_fee": "100.00",
          "minimum_order": "2000.00",
          "eta_min_minutes": 20,
          "eta_max_minutes": 45,
          "distance_basis": "delivery_base_to_precise_destination",
          "coverage_boundary_basis": "destination_centre_radius"
        }
      }
    ]
  },
  "products": [
    {
      "id": "7ea4b6b1-3c5a-4de1-9aba-3a7d28d3b810",
      "name": "Everyday Item",
      "category": {
        "id": 14,
        "name": "Everyday Essentials",
        "slug": "everyday-essentials"
      },
      "description": "A useful product.",
      "image": "https://your-inprofic-host.example/media/commerce/products/business-4/abc123.jpg",
      "image_url": "https://your-inprofic-host.example/media/commerce/products/business-4/abc123.jpg",
      "unit": "pack",
      "available_now": "18.00",
      "order_modes": [
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

### 4.1 Product-category integration

Category support is already exposed by INPROFIC. The website must consume it;
existing catalogue code will not gain category navigation automatically.

- `categories` is the ordered list of active categories that currently contain
  at least one published product.
- `product.category` is the matching `{id, name, slug}` object, or `null` for an
  uncategorised product.
- Use `category.id` as the in-memory relationship and `category.slug` for a
  readable website filter/URL.
- Include a website-owned “All” option and, when needed, an “Uncategorised”
  option for products whose `category` is `null`.
- Refresh the catalogue from INPROFIC rather than permanently copying category
  names. Renames, ordering and assignments are reflected on the next products
  response.
- Checkout item payloads do not change: continue submitting the product UUID,
  not the category ID.

Minimal grouping logic:

```js
const productsFor = (catalogue, categoryId) => catalogue.products.filter(
  product => categoryId === "all"
    || (categoryId === "uncategorised" && product.category === null)
    || String(product.category?.id) === String(categoryId)
);
```

### 4.2 Vertical-aware sales-channel language

**External-channel boundary:** never synthesize or expose a Physical Store option on a website. INPROFIC rejects `physical_store` on hosted-storefront, API and connector checkout creation even if an older/custom client submits it. This prevents a lower/different in-premise price from leaking into Online checkout. Distribution/bulk is deliberately available on both external and POS surfaces subject to the product minimum.

The channel codes are stable integration keys, but their labels are selected
for the tenant's business vertical. Always submit the code and display the
returned label from each product's `order_modes` array.

| Stable code | Example labels returned by INPROFIC |
| --- | --- |
| `online` | Online Order, Delivery / Online Order, Online Trade Order |
| `distribution` | Distribution Order, Catering / Bulk Order, Wholesale / Customer Order, Wholesale Order, Bulk Customer Order |

`physical_store` is intentionally not exposed by the hosted storefront or headless API. It is reserved for INPROFIC's staff-operated in-premise POS, where the physical-store/direct price actually applies.

Do not hard-code “retail,” “catering,” “wholesale,” or “pre-order” based only on
the code. Use:

- `order_modes[].code` for checkout `order_mode` and internal website logic;
- `order_modes[].label` for buttons, basket summaries and customer history;
- `order_modes[].fulfilment_mode` to explain whether this particular option is
  fulfilled from `stock` or as a `preorder`;
- top-level `service` only as descriptive business context, not as routing
  logic.

No image produces empty `image` and `image_url` strings. Use `image` in new code.

External storefront/headless mode codes are `online` and `distribution`. Display the returned vertical-specific `label`. Distribution/bulk remains available on external surfaces whenever the product enables it, with its configured minimum quantity enforced by INPROFIC. The in-premise POS additionally supports the tenant vertical's direct/physical-store channel. Production services normally use `preorder` fulfilment for online/distribution; wholesale and retail remain stock-based and are never forced through production.

The hosted catalogue hides product counts. A headless website may similarly use `available_now` only for validation/UI disabling. INPROFIC always rechecks it during checkout.

`order_modes` is authoritative. The legacy `ordering_modes`, `preorder_price`, and `preorder_min_quantity` aliases remain for website compatibility and refer only to the Online path. `stock_price` is no longer returned externally because it is an in-premise POS price. New code should use `order_modes`.

## 5. Delivery discovery and quote

Read the top-level `delivery` object from the product response on every
catalogue refresh. Show delivery only when `delivery.enabled` is `true`. Render
the returned areas instead of copying zone IDs or names into website code.

### 5.1 What the headless website must implement

The website UI should mirror the hosted/POS flow, but INPROFIC remains the
validation and pricing authority:

1. show the returned destination areas in a selector;
2. draw each area's `coverage_polygon` on the website map as a visual guide.
   The polygon already includes the radius and optional diagonal extensions, so
   the website should **not** recreate INPROFIC's geometry algorithm;
3. after an area is selected, ask for a **precise delivery address** (building,
   street, locality and useful landmark);
4. request a quote first with `area_id + address` and no coordinates. INPROFIC
   geocodes the address and verifies that the result is inside that area;
5. if the quote endpoint returns a location error, keep the basket intact and
   show the returned `detail`. Let the customer correct the address **or** open
   the website map and place an exact destination pin;
6. when the customer uses the pin, resend the same address plus the pin's
   `latitude` and `longitude`. INPROFIC still checks the pin against the selected
   area boundary;
7. display the chosen quote fee, distance, ETA and any Hybrid switch-policy text
   before payment; and
8. submit only INPROFIC's returned `delivery_quote_id` with checkout. Do not
   independently calculate or persist a delivery fee.

A basic map implementation can use Leaflet, MapLibre, Google Maps, or another
map library. The only required geometry input is the returned
`coverage_polygon`: convert each point to `[latitude, longitude]` (or the
coordinate order required by the chosen library), draw a non-editable polygon,
and highlight the currently selected area's polygon. The website may provide its own address autocomplete for convenience, but it should match the hosted INPROFIC behavior: wait for **2,000 ms of continuous idleness** after the latest address edit before requesting suggestions/location resolution. Every keystroke, deletion, paste or correction resets the timer. Cancel any older in-flight lookup where possible, or ignore its eventual response if the input has changed, so stale suggestions cannot replace the corrected address. This autocomplete behavior **does not replace** the server quote validation.

After the customer chooses one order mode and completes the basket, calculate
the candidate subtotal from that mode's current `order_modes[].price` using
decimal arithmetic. Then request the authoritative delivery options:

```http
POST /api/v1/storefronts/{business_slug}/delivery/quote
X-INPROFIC-Key: <tenant API key>
Content-Type: application/json
```

```json
{
  "subtotal": "2000.00",
  "address": "12 Example Street, Victoria Island",
  "area_id": 8,
  "latitude": "6.4282500",
  "longitude": "3.4221500"
}
```

Rules:

- `subtotal` and `address` are required;
- for the normal named-area flow, send `area_id + address` first and omit
  coordinates so INPROFIC can validate the typed address;
- send both `latitude` and `longitude` only when the customer deliberately uses
  the map-pin fallback (or when a trusted first-party location picker already
  established the exact point);
- when area and coordinates are supplied, the area keeps its configured pricing
  rules and coverage boundary while the coordinates determine the precise
  destination and billable base-to-destination distance;
- do not expose the API key by calling this endpoint directly from browser
  JavaScript—proxy it through the website server;
- a basket, order-mode or destination change invalidates the quote;
- a quote expires at `expires_at` and may belong to only one live checkout.

When tenant policy chooses the method automatically, the response has a
top-level `quote_id`:

```json
{
  "quote_id": "1e75ac66-66b1-4f31-91ec-2238c7bc0be0",
  "selection_required": false,
  "provider": "inhouse",
  "provider_label": "In-house delivery",
  "distance_km": "4.30",
  "fee": "1500.00",
  "total": "3500.00",
  "eta_min_minutes": 25,
  "eta_max_minutes": 50,
  "expires_at": "2026-09-16T15:20:00+01:00",
  "destination": {
    "address": "12 Example Street, Victoria Island",
    "latitude": "6.4282500",
    "longitude": "3.4221500",
    "area_id": 8,
    "area_name": "Victoria Island",
    "validated_by": "geocoded_address"
  },
  "options": [
    {
      "quote_id": "1e75ac66-66b1-4f31-91ec-2238c7bc0be0",
      "provider": "inhouse",
      "provider_label": "In-house delivery",
      "distance_km": "4.30",
      "fee": "1500.00",
      "total": "3500.00",
      "eta_min_minutes": 25,
      "eta_max_minutes": 50,
      "expires_at": "2026-09-16T15:20:00+01:00"
    }
  ]
}
```

For customer-choice Hybrid routing, `selection_required` is `true` and the
top-level `quote_id` is `null`. Render every object in `options`, let the
customer choose, and submit that option's `quote_id`. Display
`switch_policy_text` near the options when present. Never infer the provider or
replace the returned fee.

If quoting fails, do not create a delivery checkout or start payment. Let the
customer correct the destination, refresh the basket, choose pickup, or contact
the tenant. For address-location failures, the normal recovery is to show the
exact INPROFIC `detail` message and offer the map-pin fallback.

The returned `destination.validated_by` is `geocoded_address` when INPROFIC
located the typed address, and `map_pin` when coordinates were supplied as the
fallback. A website may display this as “Address located” or “Map pin validated.”
It is informational only; possession of a quote ID is the authoritative proof
that the destination passed delivery validation.

### Keep delivery details on the final review screen

Do not stop showing delivery details after the quote-selection step. When the customer proceeds to the last review screen before payment, show the accepted delivery method/provider, ETA range, distance, selected delivery area, precise validated destination, validation method (`geocoded_address` or `map_pin`), delivery fee, and `switch_policy_text` when present. Show these beside the product lines, unit prices, subtotal and final total.

After checkout creation, use the checkout response itself as the final source of truth. Delivery checkouts now include an accepted delivery snapshot under `delivery`:

```json
{
  "checkout_id": "...",
  "subtotal": "2000.00",
  "delivery_fee": "1500.00",
  "amount": "3500.00",
  "delivery": {
    "quote_id": "1e75ac66-66b1-4f31-91ec-2238c7bc0be0",
    "provider": "inhouse",
    "provider_label": "In-house delivery",
    "selection_source": "platform",
    "routing_policy": "fastest_eta",
    "switch_policy": "business_absorbs",
    "switch_policy_text": "The business may switch between in-house delivery and its delivery partner after payment. Your paid delivery fee will not increase; the business absorbs any higher provider cost.",
    "distance_km": "4.30",
    "fee": "1500.00",
    "total": "3500.00",
    "eta_min_minutes": 25,
    "eta_max_minutes": 50,
    "destination": {
      "address": "12 Example Street, Victoria Island, Lagos, Nigeria",
      "latitude": "6.4282500",
      "longitude": "3.4221500",
      "area_id": 8,
      "area_name": "Victoria Island",
      "validated_by": "geocoded_address"
    }
  }
}
```

The same `delivery` object is returned by `GET /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}`. Use it when restoring a checkout after refresh/login/device state loss. It prevents the website from having to reconstruct delivery review data from local state.

For Hybrid customer-choice, the website first presents `options[]` from the quote endpoint. Once the selected `quote_id` is submitted during checkout creation, the resulting `delivery` object represents the one accepted method and is what should remain visible on the final review/payment screen.

`reservation_expires_at` is the payment/reservation deadline for the created checkout. Treat it separately from the earlier quote's `expires_at`: once a valid quote has been attached to a checkout, render the checkout snapshot and reservation deadline rather than asking the customer to interpret the old quote expiry.

## 6. Payment discovery

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
    {"code": "transfer", "label": "Transfer", "requires_payment_proof": true, "confirmation": "staff_review"},
    {"code": "bank_transfer", "label": "Instant bank transfer (Monnify)"}
  ]
}
```

Public/headless codes can be `paystack`, `monnify`, `transfer` and `bank_transfer`. `transfer` is the native no-gateway option; `bank_transfer` is the provider-backed temporary-account option. **Cash and `pos_card` are never returned on this surface.** Render only what is returned. INPROFIC revalidates eligibility when payment starts.

## 7. Create checkout

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
  "delivery_quote_id": "1e75ac66-66b1-4f31-91ec-2238c7bc0be0",
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
- a delivery checkout must submit the selected, unexpired `delivery_quote_id`;
- INPROFIC uses the quote's destination as the authoritative delivery address;
- omit both `service_mode: "delivery"` and `delivery_quote_id` for pickup/non-delivery checkout;
- never send price, amount or total.

INPROFIC records the customer name in the tenant audit trail, snapshots authoritative pricing and creates no Intake, Sale or Production record yet.

HTTP `201` means created; `200` means an idempotent retry returned the existing checkout:

```json
{
  "checkout_id": "649948bd-613f-4d72-821e-7d99ae066d52",
  "status": "awaiting_payment",
  "order_mode": "online",
  "fulfilment_mode": "stock",
  "subtotal": "2000.00",
  "delivery_fee": "1500.00",
  "delivery_quote_id": "1e75ac66-66b1-4f31-91ec-2238c7bc0be0",
  "delivery": {
    "quote_id": "1e75ac66-66b1-4f31-91ec-2238c7bc0be0",
    "provider": "inhouse",
    "provider_label": "In-house delivery",
    "selection_source": "platform",
    "routing_policy": null,
    "switch_policy": null,
    "switch_policy_text": "",
    "distance_km": "4.30",
    "fee": "1500.00",
    "total": "3500.00",
    "eta_min_minutes": 25,
    "eta_max_minutes": 50,
    "destination": {
      "address": "12 Example Street, Victoria Island, Lagos, Nigeria",
      "latitude": "6.4282500",
      "longitude": "3.4221500",
      "area_id": 8,
      "area_name": "Victoria Island",
      "validated_by": "geocoded_address"
    }
  },
  "amount": "3500.00",
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

Save `checkout_id`, amount/currency, external ID and checkout idempotency key in the website database/session before payment. For a delivery checkout, render the returned `delivery` object on the final review screen and persist only what your website needs for presentation/recovery; INPROFIC remains authoritative when the checkout is fetched again.

If INPROFIC pricing, availability or payable quantity changed after quoting,
checkout returns an error rather than accepting a mismatched fee. Refresh the
catalogue and delivery quote with a new checkout idempotency key.

## 8. Checkout errors

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

## 9. Initialize payment

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

Gateway-backed new `bank_transfer` payments reject manual claims.

### Transfer (no gateway)

```json
{"method": "transfer"}
```

This method does not call Paystack, Monnify, or another payment gateway. INPROFIC returns the business's configured static account details and marks `proof_required: true`:

```json
{
  "method": "transfer",
  "status": "awaiting_customer",
  "gateway_provider": "",
  "authorization_url": "",
  "proof_required": true,
  "bank_account": {
    "bank_name": "Example Bank",
    "account_name": "Example Business Ltd",
    "account_number": "0123456789",
    "account_expires_at": null,
    "display_text": "Use your order name as narration where possible."
  },
  "claim": null
}
```

Show the exact amount returned by INPROFIC together with those account details. Once the customer has made the transfer, upload the evidence through the website's own backend/serverless route; do not expose the `X-INPROFIC-Key` in browser JavaScript:

```http
POST /api/v1/storefronts/{business_slug}/checkouts/{checkout_id}/payments/current/claim
X-INPROFIC-Key: <tenant API key>
Content-Type: multipart/form-data

payer_name=<customer name>
transfer_reference=<bank/reference value>
payment_proof=<JPG|JPEG|PNG|WEBP|PDF file, max 10 MB>
```

Both `payer_name` and `transfer_reference` are required, and `payment_proof` is required for every newly created native `transfer` claim. A successful claim moves the payment to `awaiting_verification`. The serialized claim intentionally returns `proof_received: true/false` rather than a public media URL. An authorized INPROFIC staff member reviews the evidence and verifies the actual bank credit before settlement; only then can the checkout materialize an order. Poll the normal current-payment endpoint while review is pending.

Inside INPROFIC, that pending claim is surfaced to authorized Commerce staff in the shared movable alert tray. If the business enables repeating Commerce sounds, the payment-attention sound repeats at the configured interval while the notification remains unread. This operator alert is deliberately separate from the API payment state: headless clients must continue polling the authoritative payment endpoint and must never treat an alert or sound as proof of verification.

For authenticated in-premise POS, `transfer` uses the same static account configuration but follows the cash-style staff confirmation guard. The staff operator confirms only after verifying the business bank account; no customer proof upload is required on that trusted staff surface.

Historical non-gateway `bank_transfer` records can still use the claim endpoint for compatibility, but new integrations should use the explicit `transfer` code for manual/no-gateway bank transfer.

### Cash and physical POS

Cash and `pos_card` are deliberately unavailable to public/headless clients. Cash is accepted only by authenticated staff with the supplemental **Commerce storefront access** capability on the in-premise POS screen. Physical card-terminal automation currently uses a configured Paystack Terminal and settles only after provider verification.

## 10. Poll before an order exists

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

## 11. Checkout-to-order transition

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

## 12. Customer transaction history

Persist checkout history immediately after checkout creation, before gateway redirect:

```json
{
  "customer_or_session_id": "website-owned-id",
  "inprofic_checkout_id": "649948bd-613f-4d72-821e-7d99ae066d52",
  "inprofic_order_id": null,
  "inprofic_order_number": null,
  "external_order_id": "WEB-8821",
  "amount": "2000.00",
  "currency": "NGN",
  "checkout_status": "awaiting_payment",
  "payment_status": null
}
```

Update it after every poll. For anonymous users, use a signed HTTP-only website session and optionally device-local links. Do not expose a phone-only public lookup without OTP; it can leak other customers’ orders.

The hosted INPROFIC catalogue already saves tenant-specific unguessable tracking links in that browser. A headless website owns its own history UI.

### 12.1 Customer login and account ownership

Customer login is optional; guest checkout remains valid.

INPROFIC currently provides a complete tenant-scoped customer account on its
hosted storefront:

```text
GET|POST /shop/{business_slug}/account/register/
GET|POST /shop/{business_slug}/account/login/
POST     /shop/{business_slug}/account/logout/
GET|POST /shop/{business_slug}/account/
```

These are browser HTML/session routes, not headless JSON endpoints. They use an
INPROFIC session cookie, never the website's `X-INPROFIC-Key`. Accounts are
separate from staff users, scoped to one tenant, and provide saved contact and
address details plus that customer's checkout history.

For a fully headless website, the website remains the customer identity
provider:

1. authenticate the customer using the website's normal login/session system;
2. keep the INPROFIC API key on the website server;
3. send the signed-in customer's current name, phone, email and address as the
   checkout `customer` snapshot;
4. persist INPROFIC `checkout_id` and later `order_id` against the website's
   customer ID;
5. render history by polling the authenticated checkout/order endpoints from
   the website server.

Do not post customer passwords to the Commerce API and do not treat an email or
phone number alone as authorization to view history. If the website does not
already have customer authentication, link customers to the hosted INPROFIC
account pages instead of inventing an insecure public lookup.

## 13. Idempotency and reservations

- Reuse a checkout key only for the same logical basket retry.
- Use a separate stable payment key per logical attempt.
- Use a new payment key when deliberately changing/retrying a failed method.
- Never reuse a key for another basket or payment method.
- Keep `external_order_id` unique for the website order within that tenant/source.

Duplicate retries/callbacks cannot materialize two intakes for one checkout.

Stock fulfilment is reserved at checkout. The configurable hold is 5–120 minutes (default 15). Active holds reduce web and direct/POS availability. Unpaid expiry releases stock. Production/preorder channels preserve production demand instead.

Late verified payment or a safe-materialization failure becomes `paid_review`: financial truth remains, staff can recover without charging again, and the website must show “paid, under review.”

## 14. Server-side reference client

This JavaScript belongs on the website server, not in the browser:

```js
const base = process.env.INPROFIC_BASE_URL;
const slug = process.env.INPROFIC_BUSINESS_SLUG;
const apiKey = process.env.INPROFIC_API_KEY;

async function inprofic(path, { method = "GET", body, idempotencyKey } = {}) {
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
  inprofic("/checkouts", { method: "POST", body: input, idempotencyKey: key });

export const startPayment = (checkoutId, input, key) =>
  inprofic(`/checkouts/${checkoutId}/payments`, { method: "POST", body: input, idempotencyKey: key });

export const resolveDeliveryLocation = input =>
  inprofic("/delivery/location", { method: "POST", body: input });

export const quoteDelivery = input =>
  inprofic("/delivery/quote", { method: "POST", body: input });

export const checkoutStatus = checkoutId =>
  inprofic(`/checkouts/${checkoutId}/payments/current`);

export const orderStatus = orderId => inprofic(`/orders/${orderId}`);

export const deliveryTracking = deliveryId =>
  inprofic(`/deliveries/${deliveryId}/tracking`);
```

The browser calls the website’s own API routes; the website server attaches the secret.

## 15. Go-live checklist

- [ ] INPROFIC uses HTTPS and correct `ALLOWED_HOSTS`.
- [ ] `MEDIA_ROOT` is persistent and `/media/` is mapped on PythonAnywhere.
- [ ] Commerce module, master switch and Headless API are enabled.
- [ ] Delivery base pin, price bands, destination centres/radii/extensions and dispatch method are tested before enabling Delivery.
- [ ] Product response `delivery.enabled`, `areas`, `coverage_polygon`, `location_url` and `quote_url` drive the website UI.
- [ ] The website keeps precise address and map pin synchronized in both directions through its server-side proxy to `delivery/location`, and offers map-pin fallback when INPROFIC cannot locate typed text.
- [ ] Delivery quote is requested server-side after basket/order-mode selection and refreshed after any basket or destination change.
- [ ] Customer-choice Hybrid renders the returned options and switch-policy text.
- [ ] Selected `delivery_quote_id` is included in checkout and returned subtotal, delivery fee and amount are displayed before payment.
- [ ] Final business slug matches website and gateway configuration.
- [ ] Published products return absolute working `image` URLs.
- [ ] Active product categories are rendered from top-level `categories`, with uncategorised products handled explicitly.
- [ ] All three applicable channel codes, labels, prices and limits are tested.
- [ ] Customer-facing channel text uses `order_modes[].label`; checkout submits `order_modes[].code`.
- [ ] API key exists only in website server secrets.
- [ ] Only configured payment methods appear.
- [ ] Gateway webhooks point directly to INPROFIC.
- [ ] Checkout requires name/phone; email is optional until a gateway requires it.
- [ ] Website stores checkout history before redirect.
- [ ] Customer history is protected by website login/session, or customers use the hosted INPROFIC account pages.
- [ ] Browser return starts polling and never confirms payment.
- [ ] Delivery tracking is fetched through the website backend from `/deliveries/{delivery_id}/tracking`; the INPROFIC API key is never exposed to the browser.
- [ ] Customer timeline begins at order confirmation; pending/assigned/ready/picked-up/out-for-delivery/delivered states are rendered from INPROFIC values rather than invented locally.
- [ ] Delivery ETA is shown only after `picked_up_at` exists, using `eta_min_at`/`eta_max_at`; before pickup the website explains that ETA starts at pickup.
- [ ] Realtime `delivery.changed` is treated only as a wake-up signal followed by an authoritative tracking re-fetch, with an 8–10 second polling fallback if no relay/socket is available.
- [ ] `paid_review` prevents repeat charging.
- [ ] Order UUID and display number are stored separately.
- [ ] Duplicate requests and cross-tenant UUID/key attempts are tested.
- [ ] Public/headless methods contain no cash/POS option.
- [ ] Native `transfer`, when enabled, displays only the tenant-configured static account, submits required proof as multipart through the website backend, and remains pending until staff verification.
- [ ] Instant `bank_transfer` displays the provider-issued temporary account and settles only after webhook + provider verification.
- [ ] Cash and physical terminal payments are tested only from the authenticated in-premise Storefront POS; POS `transfer` is staff-confirmed like cash and does not require customer proof.

## 16. Earlier integration compatibility

`POST /api/v1/storefronts/{business_slug}/orders` is now **retired for writes**. It returns HTTP `410` with `code: "checkout_first_required"` and points the caller to `/checkouts`. This prevents any new integration from materializing an intake before payment.

Read/status and payment routes for already-existing historical intake UUIDs remain available during migration:

```text
GET  /api/v1/storefronts/{business_slug}/orders/{order_id}
POST /api/v1/storefronts/{business_slug}/orders/{order_id}/payments/initiate
GET  /api/v1/storefronts/{business_slug}/orders/{order_id}/payments/current
POST /api/v1/storefronts/{business_slug}/orders/{order_id}/payments/current/claim   # historical non-gateway records; use checkout-scoped claim for new native `transfer`
```

For every new or migrated website: create `/checkouts`, pay using the checkout UUID, poll until verified settlement returns an `order_id`, then begin order tracking.


## 17. Delivery rider and storefront-customer boundaries

Delivery Rider is a purpose-specific staff access surface: rider-only users can see and act only on assignments linked to their own active in-house driver record. Delivery alerts reuse the durable Commerce notification transport but support targeted rider recipients. Public storefront customers remain guests by default; optional `StorefrontCustomer` profiles are tenant-scoped, separate from staff users, and store purchase history without making registration a checkout requirement. Hybrid delivery means in-house and the configured provider remain interchangeable per order under the tenant’s routing and customer-visible post-payment switch policy.

## Two-way delivery address/map synchronization

For delivery UI, do not let the address textbox and map pin drift apart. Use `POST /api/v1/storefronts/{business_slug}/delivery/location` in both directions: send `area_id + address` to obtain validated coordinates and move/zoom the map pin; send `area_id + latitude + longitude` to reverse-geocode a manually placed pin and update the address field. For typed addresses, wait until the customer has stopped editing for **2 seconds**; every correction resets the debounce window, and an older request/result must be cancelled or ignored after the text changes. Because this endpoint requires `X-INPROFIC-Key`, the browser should call the website's own backend/serverless proxy; that proxy calls INPROFIC and returns only the normalized location result. Never expose the tenant API key in browser JavaScript. Then send the synchronized address, coordinates and returned `location_source` to `/delivery/quote`. Coverage and minimum-order errors returned by INPROFIC should be displayed as red customer-facing validation text. See `COMMERCE_INTEGRATION.md` for the complete payload/response contract.

## 18. Live delivery status, customer updates and pickup-based ETA

Once a paid delivery order exists, store the returned delivery UUID alongside the order UUID. Fetch the current customer-safe delivery state from the website server:

```http
GET /api/v1/storefronts/{business_slug}/deliveries/{delivery_id}/tracking
X-INPROFIC-Key: <server-side secret>
```

A website backend helper can reuse the client above:

```js
export const deliveryTracking = deliveryId =>
  inprofic(`/deliveries/${deliveryId}/tracking`);
```

Return only the customer-safe response from your own backend route to browser JavaScript. The response contains:

- `order`: confirmation/order state and confirmation time;
- `delivery`: current status, driver/provider display, pickup time, pickup-anchored ETA window, delivered time and provider tracking URL when available;
- `timeline`: confirmation plus each delivery event in chronological order;
- `realtime`: change-event metadata and fallback polling guidance.

Before pickup, `picked_up_at`, `eta_min_at` and `eta_max_at` are null. Display wording such as **“ETA starts when your order is picked up.”** Do not start a countdown from payment/confirmation. When the first pickup/out-for-delivery transition occurs, INPROFIC anchors the ETA range to the pickup timestamp using the accepted delivery quote.

Recommended customer timeline presentation:

```text
Order confirmed     -> confirmation/check indicator
Pending             -> waiting/clock indicator
Assigned            -> rider/assignment indicator
Ready                -> package/ready indicator
Picked up            -> motorbike indicator
Out for delivery     -> route/movement indicator
Delivered            -> checkmark indicator
Failed/returned      -> warning/return indicator
```

Hosted INPROFIC pages receive a customer-safe WebSocket event named `delivery.changed`, but the event intentionally contains no order/customer details. It means **re-fetch the status snapshot now**. The same transport is triggered for dispatcher updates, rider updates and supported provider/webhook updates.

For external/headless websites, do not place `X-INPROFIC-Key` in the browser. The most portable approach is for your backend to poll INPROFIC and expose a customer-authenticated/session-protected tracking route to the browser every 8–10 seconds. If your website backend can maintain or proxy the INPROFIC WebSocket and your deployment/origin policy permits it, use `delivery.changed` to trigger an immediate backend re-fetch and relay the refreshed state with your own SSE/WebSocket channel. Keep polling as a fallback.

A persistent customer “Order updates” tray can be built entirely from `timeline`: retain the last-seen event keys in the customer's website session/profile/local storage and mark newly returned timeline events unread. No INPROFIC staff account or storefront-customer registration is required for this presentation.


## Standard portions, composed products and Bulk Packs

INPROFIC can keep a Finished Good in its operational production/stock unit while exposing a friendlier customer unit. For example, a restaurant may produce Jollof Rice in `scoop` units while the storefront sells a standard `plate`. The private base conversion (for example, `1 plate = 3 scoops`) is never sent to a hosted/headless customer.

`GET /api/v1/storefronts/{business_slug}/products` returns the customer-safe fields:

```json
{
  "id": "<product uuid>",
  "name": "Jollof Rice",
  "unit": "plate",
  "contents": [
    {"name": "Jollof Rice", "quantity_label": "", "kind": "base_product"},
    {"name": "Chicken", "quantity_label": "1 piece", "kind": "finished_good"}
  ],
  "bulk_packs": [
    {
      "id": "<bulk-pack uuid>",
      "name": "2 L Bowl",
      "customer_quantity": "2.00",
      "customer_unit": "litre",
      "price": "9000.00",
      "min_order_quantity": "1.00",
      "contents": []
    }
  ]
}
```

`contents` is intentionally presentation-safe: internal scoop/ladle/ml conversions and private material quantities are not exposed unless the business explicitly supplied a public quantity label. Raw/packaging materials included in a composed product are not published automatically.

A published Finished Good can also expose `individual_options[]` without duplicating its stock or recipe. This is useful when a composed item (for example a Jollof + Chicken plate) must coexist with plain/add-on choices such as Extra Jollof Rice or Single Chicken. Each individual option returns its own public `id`, `name`, `unit`, `contents` and external `order_modes`. For renderers that want every purchasable choice already flattened, use the top-level `catalogue_items[]`; entries have `kind: "standard_product"` or `kind: "individual_option"`.

Example individual option entry:

```json
{
  "id": "individual:<option uuid>",
  "kind": "individual_option",
  "product_id": "<parent product uuid>",
  "individual_option_id": "<option uuid>",
  "name": "Extra Jollof Rice",
  "unit": "serving",
  "contents": [{"name": "Jollof Rice", "kind": "base_product"}],
  "order_modes": [
    {"code": "online", "label": "Online Order", "price": "2500.00", "min_quantity": "1.00"}
  ]
}
```

For a normal Online item, submit the existing `product_id` + `quantity`. For an individual/plain option, submit the parent `product_id`, its `individual_option_id`, and `quantity`:

```json
{
  "order_mode": "online",
  "items": [
    {"product_id": "<product uuid>", "individual_option_id": "<option uuid>", "quantity": 2}
  ]
}
```

Do not submit `bulk_pack_id` on the same line as `individual_option_id`. The option is accepted only when it is enabled and priced for the selected external channel. Physical Store availability/pricing for the same option stays private to INPROFIC POS and is never returned by this API.

For a Distribution/Bulk pack, submit its public `bulk_pack_id` on that line:

```json
{
  "order_mode": "distribution",
  "items": [
    {"product_id": "<product uuid>", "bulk_pack_id": "<bulk-pack uuid>", "quantity": 3}
  ]
}
```

A `bulk_pack_id` is rejected outside the Distribution/Bulk channel. External storefront/headless contracts continue to expose only **Online** plus the vertical-specific Distribution/Bulk channel; `physical_store` and its price remain exclusive to the in-premise POS. Use the `order_modes[].label` value returned by INPROFIC instead of hardcoding the word “Distribution” (restaurants, for example, receive `Catering / Bulk Order`).

The `contents` array is informational for customers; INPROFIC separately snapshots and consumes the applicable internal package components when fulfilment occurs. Headless clients must not attempt to calculate or deduct component stock themselves.
