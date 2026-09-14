# Subscription Plans, Services, and Founder Access

## Safety boundary

`BusinessModuleAccess` remains the runtime commercial entitlement boundary. Plan changes write explicit rows for every module. `enabled=False` is a hard ceiling before role or user permissions are evaluated.

Existing businesses are **not** automatically enrolled by the subscription migration. They keep their current existing entitlement behavior. New signups start a 30-day STARTER trial, and an existing tenant becomes plan-managed when it deliberately chooses a plan.

## Seeded plans

| Module | STARTER | PRODUCTION | BUSINESS PRO |
|---|---|---|---|
| Dashboard | Full | Full | Full |
| Inventory | Full | Full | Full |
| Procurement | — | Full | Full |
| Production | — | Full | Full |
| Sales | Full | Full | Full |
| Expenses | Full | Full | Full |
| Finance | — | — | Full |
| Reports | Basic | Full | Full |
| Users | Full | Full | Full |
| Commerce | — | — | Full |

Every plan has one 30-day trial period. A tenant can switch the plan used during that trial without restarting the 30-day clock.

STARTER Basic Reports currently means the Reports landing page plus Stock CSV and dated Sales CSV. Excel, procurement/production/finance/adjustment exports and full JSON backup require the `reports_full` feature granted by PRODUCTION/BUSINESS PRO.

## Pricing

No plan amounts were supplied in the product requirements, so migrations do not invent commercial prices. Seeded prices start at 0.00 and the superuser sets commercial pricing from Founder Console. The Founder Console saves all plan pricing together: monthly price, yearly-payment discount, and additional-service discount.

Every plan supports monthly and yearly billing. `yearly_discount_percent` is founder-configurable per plan and is applied to 12 months of the full subscription total (primary service plus discounted additional services).

Each plan also has `additional_service_discount_percent` (seeded to 30%, editable by superuser). An additional service/business profile costs:

```text
plan monthly price × (1 − additional service discount %)
```

The subscription monthly total is the primary plan price plus the discounted add-on price for every additional service profile.

## Multiple services under one subscription

The internal database field `Business.vertical` is retained for migration/backward compatibility, but user-facing product language is **Service**.

A `BusinessSubscription` owns a primary Business profile. `SubscriptionService` can attach additional Business rows to the same subscription. Each service therefore keeps a separate:

- business name and brand profile;
- inventory;
- customers and pricing;
- production/market-stock capability according to that service type;
- sales, finance and audit data.

Adding a service provisions a new Business and copies active primary-business memberships by system-role key. Users then switch business/service profiles through the existing business switcher.

Plan entitlements are applied to every service profile in the subscription. Service capability still matters: for example, Wholesale/Retail do not expose Production merely because the commercial plan contains the Production module.

## Trial, paid and expiry behavior

`BusinessSubscription` supports:

- Free trial;
- Paid;
- Expired;
- Founder lifetime.

When the effective expiry is within seven days, the persistent plan tag turns red. During trial it explicitly reads **PLAN · Trial · ends DATE**; after payment it reads **PLAN · Paid until DATE**; founder grants read **PLAN · Founder lifetime**. Business Admins can open **Plan & Billing** at any time, not only near expiry.

`python manage.py sync_subscriptions` should run daily in production. It marks elapsed subscriptions Expired and refreshes module rows. Runtime permission checks also enforce elapsed expiry immediately, so the scheduled job is not the only protection.

An expired tenant retains Dashboard and User/plan-management access so it can sign in and renew; paid operational modules are blocked.

## Subscription payment gateways

Plan billing is initiated inside INPROFIC and supports **Paystack** and **Monnify**. INPROFIC creates an immutable `SubscriptionPayment`, initializes the provider transaction server-side, and redirects the tenant to the provider-hosted checkout. Provider secret keys are never sent to the browser.

The Founder Console has independent Paystack and Monnify switches for new plan
checkouts. A disabled provider is removed from the tenant billing page and is
rejected server-side if a stale form still submits it. Disabling a provider
does not disable callback or webhook verification for existing attempts, so an
in-flight payment can still be reconciled safely.

A browser redirect is not proof of payment. INPROFIC verifies the transaction server-to-server before calling the same idempotent `mark_payment_paid()` entitlement service. Provider webhooks use signature validation and then perform authoritative provider verification before access is extended.

Required environment variables:

```text
PAYSTACK_SECRET_KEY=...
PAYSTACK_PUBLIC_KEY=...              # reserved for future inline UI; hosted redirect does not require it
MONNIFY_API_KEY=...
MONNIFY_SECRET_KEY=...
MONNIFY_CONTRACT_CODE=...
MONNIFY_BASE_URL=https://api.monnify.com
```

Use `https://sandbox.monnify.com` for Monnify sandbox testing. Configure the provider dashboards to send subscription events to:

```text
/users/plans/payment/webhook/paystack/
/users/plans/payment/webhook/monnify/
```

The callback routes are generated by INPROFIC during transaction initialization. Founder Console retains manual confirmation as an operational fallback for manual payment requests; online Paystack/Monnify payments should normally be confirmed automatically.

## Founder lifetime access

Global superusers have a dedicated **Founder Console** outside Django Admin. It can:

- set plan prices/additional-service discount;
- enable or disable payment channels for new subscription checkouts;
- grant any tenant lifetime free access to a specific plan;
- revoke a founder grant;
- inspect subscriptions;
- confirm pending subscription payments.

Founder lifetime access applies the selected plan's exact module matrix with `BusinessModuleAccess.source="founder"`; it is not an unrestricted bypass of module definitions.

## Billing page presentation

The tenant payment screen lists every service/business profile covered by the subscription, distinguishing the primary profile from discounted additional-service profiles. Monthly duration selections update the displayed amount immediately in the browser, while the server-side `payment_amount()` function remains authoritative when creating the Paystack/Monnify payment request. Plan and payment amounts are rendered with thousands separators and the application's IBM Plex Mono numeric style.

The plan currently providing active access is visibly marked on its card. Its renewal payment remains locked until the final seven days before expiry, including during a trial; a founder lifetime grant stays permanently non-payable on its granted plan. Other plans remain selectable, but changing away from any active trial, paid plan or founder grant requires an explicit warning acknowledgement. INPROFIC enforces these rules server-side as well as in the browser, and does not change entitlements until a provider verifies payment.
