# Delivery engine, custom providers and optional Glovo LaaS v2 adapter

INPROFIC's delivery system remains provider-neutral. INPROFIC owns the checkout delivery amount, quote snapshot, dispatch assignment, customer tracking surface, proof of delivery and immutable delivery-event timeline. Businesses can configure their own delivery partners as manual couriers or through the provider-neutral Delivery Adapter v1 contract. Glovo is only an optional built-in adapter, exposed when the Founder enables it platform-wide; it does not replace the in-house, custom-provider or hybrid delivery workflows.

## Entitlement and permissions

Delivery is controlled by the founder-managed `delivery` plan entitlement. The plan entitlement is checked before any role or user permission.

Typical access:

- **Business Admin** — configures delivery settings, origins, coverage/rate policy, drivers and provider accounts.
- **Delivery Coordinator** — operates the dispatch queue and delivery timeline.
- **Other staff** — no delivery access unless their role/user permission grants it.

Removing the plan entitlement blocks new quotes/dispatch actions even when a user still has a Delivery permission.

## Provider modes

`DeliverySettings.default_provider` supports:

- `inhouse` — INPROFIC distance/rate bands, in-house drivers and POD;
- `third_party` — a configured provider account is authoritative for dispatch;
- `hybrid` — in-house and the configured provider remain simultaneously available. Routing can be customer choice, dispatcher choice, lowest fee, fastest ETA, in-house first or delivery-partner first. A paid order resolves to a real method (`inhouse` or `third_party`); `hybrid` itself is never treated as a courier.

Custom delivery-partner accounts are always available when the Delivery module is available. They can remain manual, or use the provider-neutral API adapter for dispatch/status synchronization. A dormant built-in **Glovo** account is created only while the Founder-level Glovo switch is enabled. Existing Glovo configuration is preserved when that switch is later turned off, but it is hidden and cannot execute until re-enabled.

## Founder gate and Glovo onboarding values

The Founder Console controls whether the built-in Glovo adapter exists as an available option. The platform switch is **off by default**. Turning it off removes Glovo from tenant/customer/API-facing runtime surfaces and blocks Glovo quote, dispatch, cancellation and webhook execution without deleting tenant credentials or historical delivery records. Turning it on only makes the adapter available; each business must still configure and activate its own approved Glovo account. INPROFIC does not provide or resell Glovo accounts and does not imply official Glovo partnership status.

### Glovo onboarding values

Open **Delivery → Provider plug-ins → Glovo** and enter the values issued for that tenant/environment:

- API base URL;
- OAuth client ID (stored in `api_key`);
- OAuth client secret (stored in `api_secret`);
- Glovo LaaS Address Book pickup ID;
- a strong partner/webhook secret.

The built-in LaaS v2 endpoint defaults are:

```text
OAuth token       /oauth/token
Quote             /v2/laas/quotes
Create parcel     /v2/laas/quotes/{quote_id}/parcels
Cancel parcel     /v2/laas/parcels/{external_reference}/cancel
Tracking link     /v2/laas/parcels/{trackingNumber}/parcel-tracking-link
Webhook registry  /v2/laas/webhooks
```

The API base URL is deliberately tenant-configurable rather than hardcoded because Glovo onboarding/environment details are provider-issued.

## Live quote flow

When Glovo is selected and **Use live quotes** is enabled:

1. INPROFIC obtains an OAuth 2.0 client-credentials token.
2. It calls `POST /v2/laas/quotes` using the tenant's Address Book pickup ID and the customer delivery address/coordinates.
3. Glovo's `quoteId`, `quotePrice`, distance, expiry and ETA bounds are snapshotted on `DeliveryQuote`.
4. Tenant minimum-order policy can still be enforced when a configured area/rate band supplies one.
5. The Glovo fee is added to checkout **before payment**.
6. A Glovo-only configuration fails closed if a live provider quote cannot be obtained, preventing an undercharged checkout.
7. Hybrid mode can expose both the INPROFIC in-house quote and Glovo quote. The configured routing policy selects one automatically, asks the customer, or leaves the choice to dispatch staff. The customer-facing switch policy is shown before payment and the customer is never silently charged more after payment.

No provider network call is held inside a long database transaction.

## Paid order and automatic dispatch

A provider job is never created merely because a customer built a basket.

1. Checkout snapshots product prices and the delivery quote.
2. Trusted payment verification confirms the full amount, including delivery.
3. INPROFIC materializes the commerce order exactly once.
4. It creates a tenant-owned `DeliveryAssignment` and event timeline.
5. If the Glovo account has `auto_dispatch=True`, INPROFIC exchanges OAuth credentials for a token and creates the parcel from the previously stored `quoteId`.
6. The Glovo tracking number/provider state are stored on the assignment.
7. INPROFIC fetches the Glovo parcel-tracking link when available and exposes it through the delivery/customer tracking surfaces.

If credentials are incomplete or automatic dispatch is disabled, the INPROFIC assignment still exists and can be handled manually or in-house.

## Webhooks

The tenant webhook endpoint is:

```text
/api/v1/delivery/providers/glovo/<business-slug>/webhook
```

Glovo LaaS calls the endpoint with the configured partner secret in the HTTP `Authorization` header. INPROFIC refuses blank or unknown secrets and matches the update only inside the business that owns that provider account.

From **Delivery → Provider plug-ins**, Business Admins can use **Register / verify Glovo webhooks**. INPROFIC registers/verifies both:

- `STATUS_UPDATE`
- `POSITION_UPDATE`

The default LaaS status map is:

```json
{
  "CREATED": "assigned",
  "SCHEDULED": "assigned",
  "ACTIVATED": "assigned",
  "ACCEPTED": "assigned",
  "WAITING_FOR_PICKUP": "ready",
  "PICKED": "picked_up",
  "WAITING_FOR_DELIVERY": "out_for_delivery",
  "DELIVERED": "delivered",
  "REJECTED": "failed",
  "CANCELLED": "cancelled",
  "RETURNED": "returned"
}
```

Every inbound provider status creates a delivery event and audit-log entry. Provider callbacks update delivery state; they do not directly mutate stock, finance or production records.

## Security boundary

- Provider credentials are tenant-owned server-side fields and never rendered into the public storefront.
- Webhook authorization must match the tenant's stored partner secret.
- Delivery remains plan-gated independently of Commerce.
- Glovo remains a Founder-gated adapter. In-house, custom-provider and hybrid operation continue without it.
- Hiding Delivery through a tenant plan blocks new user actions/surfaces but does not stop status callbacks for already-created assignments; synchronization continues underneath for upgrade continuity. The Founder Glovo switch is the stronger platform kill switch and blocks Glovo callbacks when off.


## In-house rider workspace

An in-house `DeliveryDriver` can be linked to a staff login with the dedicated **Delivery Rider** role. A rider-only login lands directly on `/delivery/rider/` and sees only deliveries assigned to that linked driver inside the active tenant. The lightweight workspace exposes pickup/drop-off details, order contents, contact actions, valid status transitions, proof-of-delivery fields, issue/complaint reporting, issue responses and recent completed jobs. It does not grant Inventory, Finance, Commerce, Delivery Coordinator or Dashboard access unless another permission is explicitly added.

Assignment/reassignment, delivery status, rider issues, issue responses and provider exceptions use the same durable notification backbone as Commerce: persistent unread notices, WebSocket refresh, optional Web Push, desktop alerts and sound. Rider-only users receive direct notifications addressed to their user; dispatch/commerce staff receive the business-level delivery events their permissions allow.

## Optional storefront customer accounts

Public customers are **not required to register**. Guest checkout and public delivery tracking remain available. A customer may optionally create a storefront account to save their name, phone and default address and to view their purchase/delivery history. `StorefrontCustomer` is separate from staff authentication and is always keyed by `business`; the same email address may therefore hold independent customer profiles at different INPROFIC tenants. A customer session stores the profile identity separately per business rather than creating a global marketplace identity.
