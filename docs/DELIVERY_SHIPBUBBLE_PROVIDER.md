# Delivery engine, custom providers and the optional Shipbubble courier integration

INPROFIC's delivery system stays provider-neutral. INPROFIC owns the checkout delivery amount, the quote snapshot,
the assignment, the status history and the customer tracking page. A provider only supplies a rate, a booking and
status updates.

Three ways to deliver can be mixed per business (Hybrid):

1. **In-house riders**: your own riders, vehicles and price bands.
2. **Custom delivery partner**: a courier you coordinate manually, or one connected through the provider-neutral
   Delivery Adapter v1 contract. Customer pricing uses your own price bands.
3. **Shipbubble couriers** (optional platform integration): live courier rates at checkout and automatic booking after payment.

## Platform integration and clearance

The platform integration setting (`PlatformIntegrationSettings.shipbubble_enabled`) controls whether the adapter is
available. Disabling it blocks Shipbubble calls without deleting saved tenant configuration or delivery history.
Each tenant business uses **its own Shipbubble account and API key**; INPROFIC does not provide or resell access.

Shipbubble's current terms restrict use of its word mark and logos without written authorization and restrict public
statements acknowledging a relationship without prior written consent. Obtain written permission or specific written
guidance at the **software/platform level** before publicly naming Shipbubble in the plan matrix, product UI, or
marketing. This clearance is separate from each tenant's technical requirement to have its own Shipbubble account,
API key, and API access. See [Shipbubble Terms and Conditions](https://shipbubble.com/terms-and-conditions) and the
[Shipbubble API getting-started guide](https://docs.shipbubble.com/get-started).

## What a business configures

Open **Delivery > Setup & partners > + Provider** and choose *Shipbubble couriers*:

- **API key**: `sb_prod…` for live use, `sb_sandbox…` for testing. It is also the secret that verifies Shipbubble's callbacks.
- **Pickup address code (optional)**: leave blank and INPROFIC validates your delivery base address with Shipbubble the
  first time it is needed, then remembers the code.
- **Courier choice**: offer the *cheapest* or the *fastest* courier's rate to the customer.
- **Typical parcel weight and size**: used for rate quotes. Couriers may re-bill wrong weights.
- **Auto-dispatch**: book the courier as soon as a delivery is paid. The cost is deducted from your Shipbubble wallet.

## Checkout (before payment)

1. The customer's address is validated with Shipbubble (a customer name and phone are not known yet, so the business
   contact is used for the quote only).
2. Shipbubble returns courier rates. INPROFIC takes the courier you prefer and snapshots its **customer rate**
   (`rate_card_amount`) as the delivery fee, plus the courier, service code, ETA window and the rate token, on `DeliveryQuote`.
3. The fee is added to checkout **before payment**. A Shipbubble-only configuration fails closed if no live rate can
   be obtained. Hybrid can offer your in-house quote alongside the courier quote under the published switching policy.
4. Courier quotes are treated as motorbike delivery; a customer who needs a car is offered only in-house cars.

## After payment

1. The delivery assignment is created with the quoted courier remembered.
2. With auto-dispatch on, INPROFIC re-validates the address **with the real recipient's name and phone**, requests fresh
   rates and books the *same courier that was quoted* (falling back to your preferred courier if it is no longer offered).
3. The Shipbubble order id, status and tracking link are stored on the assignment and shown on the tracking page.
4. Cancelling the delivery cancels the Shipbubble booking, which Shipbubble only allows before it has been processed.

## Status webhook

```
POST /api/v1/delivery/providers/shipbubble/<business-slug>/webhook
```

Add this URL as your webhook in the Shipbubble dashboard. Every callback carries an `x-ship-signature` header: the
HMAC-SHA512 of the raw body keyed with your API key. Unsigned or wrongly signed callbacks are rejected (403).
`shipment.*` events update the delivery; `wallet.threshold.alert` raises a "wallet running low" notification.
Unknown shipments are acknowledged without error. Shipbubble's status words are mapped to INPROFIC delivery statuses
(for example `pending` to assigned, `in_transit` to picked up, `completed`/`delivered` to delivered).

## Existing courier account history

Legacy courier credentials are not reused for Shipbubble. Older provider rows are retained as inactive archived
delivery partners, while their quotes, assignments and history remain attached to the same records. Add and configure
the business's own Shipbubble account after the platform enables the adapter.

## Operational notes

- Keep a funded Shipbubble wallet. A booking declined for balance is reported on the delivery event log.
- Shipbubble processes rate requests made after 6 PM on the next day and accepts pickup dates a limited number of days ahead.
- In-house, custom-partner and Hybrid operation continue when the Shipbubble platform integration is disabled.
