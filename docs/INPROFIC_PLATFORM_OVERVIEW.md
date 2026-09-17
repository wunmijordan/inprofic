# INPROFIC Platform Overview

## What INPROFIC is

INPROFIC is a connected business operating system for businesses that buy materials or products, manage stock, make or prepare finished goods, sell through multiple channels, collect money, pay suppliers, deliver orders, and need a reliable record of what happened across the business.

It is designed so that procurement, inventory, production, sales, commerce, delivery, finance and audit do not operate as disconnected spreadsheets. A purchase can affect stock and supplier balances; production can consume materials and create saleable goods; a paid checkout can become a sale and delivery; and the resulting cash movement can be traced back to the commercial activity that created it.

INPROFIC supports bakery, restaurant, general-production, wholesale and retail operating styles. The same platform adapts its language and available workflows to the kind of business using it.

## One business, one connected flow

A typical INPROFIC operating cycle looks like this:

1. The business buys raw materials or resale products.
2. Receiving updates stock and preserves the purchase cost history.
3. A production business uses recipes and production inputs to determine what materials are required.
4. Production records what was actually made, what was wasted, what passed quality checks and what it cost.
5. Finished products are sold through staff workflows, the in-premise POS, the hosted storefront, Order Now links, a business-owned website using the Headless API, or supported connectors.
6. Customer payments and supplier payments flow into Finance.
7. Delivery, when required, starts only from a validated and paid checkout.
8. Managers, auditors and founders can inspect the history without rewriting completed evidence.

The objective is not simply to record transactions. It is to make the business able to explain how stock, production, customer orders, supplier obligations, money and delivery outcomes relate to one another.

## Businesses and tenant separation

Every tenant operates as its own business inside INPROFIC. Its products, customers, suppliers, users, pricing, delivery setup, payments, reports and operational records are isolated from other businesses.

A customer profile created on one tenant storefront belongs only to that tenant. A rider belongs only to the business that linked the rider login. An external auditor sees only the business whose Audit Workspace they were granted. Provider credentials such as Glovo or payment credentials remain business-specific.

A commercial subscription can control which modules a business is entitled to use. Founder-level plan controls sit above staff permissions, so a user cannot gain access to a module that the business plan itself does not include.

## Inventory and measurement

INPROFIC tracks raw materials and finished goods while separating how a material is bought from how it is stored or consumed.

For example, a business may buy flour by bag, regard each bag as 50 kilograms, and consume flour in kilograms or grams. INPROFIC keeps the conversion relationship so receiving and recipe usage remain consistent.

When the measurement basis of a live material changes, INPROFIC uses a controlled conversion workflow. Current stock, current cost and live recipe/input quantities are converted together. Completed historical stock movements and frozen cost evidence remain unchanged, so changing today's measurement setup does not rewrite yesterday's audit trail.

Sellable products can also be placed into tenant-defined Product Categories such as Meals, Drinks, Accessories or Bulk Packs. Categories are independent of whether an item is made in-house or purchased for resale and are shared by the hosted storefront, POS and product API.

## Procurement and suppliers

Procurement manages purchase orders, received quantities, supplier costs and supplier payments.

Receiving a purchase updates the appropriate inventory and cost basis. Supplier payment records then reduce the amount the business owes. Because procurement and finance are connected, the business can distinguish the cost of what it acquired from whether cash has actually been paid.

Historical purchase and material-cost evidence is retained so later price changes do not distort earlier production costing or audit review.

## Production, recipes and batches

Production-capable businesses can define recipes or bills of materials for finished goods. Recipes can include raw materials, packaging and other production inputs.

Production orders release required materials, record actual output and preserve batch evidence. The system records gross production, wastage or rejection, saleable output, yield, batch number, expiry details and a frozen material-based production cost.

Shared Production Runs allow several customer/store orders to be coordinated in one manufacturing exercise while keeping each customer's commercial order separate. Material requirements are still calculated proportionally for each order line rather than pretending every order requires a full batch.

Quality checks can be attached to production batches so users can see the inspection outcome, defects and notes associated with what was made.

## Customers, orders and sales

INPROFIC maintains reusable customer records for internal sales workflows while also preserving historical snapshots on completed commercial records.

Customer-specific product pricing can take precedence over channel pricing for applicable channels. Otherwise, the product's channel price is used, with the default selling price as the final fallback.

Sales and customer payments track what was sold, what has been paid and what remains receivable. Partial payment does not falsely mark the full balance as received.

## Commerce and customer ordering

Commerce is the shared boundary for public and staff checkout flows. It supports:

- the hosted tenant storefront;
- Order Now links;
- the Headless Commerce API for a tenant-owned external website or app;
- platform/connectors that submit normalized orders into INPROFIC; and
- the authenticated in-premise POS.

Public checkout is payment-first. INPROFIC validates the basket, calculates authoritative prices and delivery charges where applicable, creates a checkout, and waits for trusted payment confirmation. Operational sales/orders are materialized only after verified settlement.

This prevents an abandoned or unpaid web basket from altering inventory, production, finance or delivery records.

## In-Premise POS

The In-Premise POS is a focused staff storefront for counter sales. It can be used in two ways:

- a dedicated POS-only staff role can log in and land directly in the POS without entering the wider business workspace; or
- POS access can be added to a manager, accountant or other staff member without replacing their normal primary role.

The POS uses the same product catalogue, product categories, pricing truth, payment controls and delivery quoting service as the rest of Commerce.

## Delivery

Delivery is a plan-gated, provider-neutral subsystem. INPROFIC owns the customer delivery quote, fee snapshot, assignment, status timeline, proof of delivery, customer tracking and audit history. The actual transport can be handled by the tenant's own riders or by an external provider plug-in.

### Delivery areas and radius coverage

A tenant first maps a Delivery / Office Base from which orders are dispatched.

Each named Delivery Destination or Zone then has:

- a central map point;
- a normal coverage radius in kilometres;
- optional north-east, south-east, south-west and north-west extensions for corridors that a simple circle cannot cover cleanly; and
- a linked pricing band containing the base fee, per-kilometre rate, minimum basket and ETA rules.

The mapped radius and optional extensions decide whether the customer's destination is inside that zone. The linked pricing band does not override that boundary.

The fee is calculated from the actual distance between the dispatch base and the customer's precise validated destination. This means the radius answers “can we serve this point?” while the base-to-destination distance answers “what distance should we price?”

The Delivery setup screen includes a graphical map editor for moving the zone centre, resizing the radius and adjusting diagonal extensions. The configured coverage shapes are then displayed on hosted and POS checkout maps as visual guidance.

### Precise address validation

After a delivery area is selected, the customer or staff user enters the precise delivery address.

INPROFIC first attempts to locate that address and checks whether the returned location falls inside the selected coverage shape. If the address cannot be located accurately, checkout asks the user to correct it or place the destination pin manually on the map.

A manual map pin is still checked against the selected delivery area's radius/extensions, so the fallback cannot bypass coverage rules.

The same validation is used by the hosted storefront, in-premise POS and Headless API.

### In-house delivery

Tenants can register their own riders or drivers. A rider can be linked to a dedicated INPROFIC staff login and receives a lightweight My Deliveries workspace rather than the normal business dashboard.

The rider sees only deliveries assigned to that rider profile, together with the information needed to complete the job. The rider can update allowed delivery stages, record proof details where required, and lodge delivery issues or complaints for dispatch staff to review.

### Glovo and external providers

The delivery engine is not built around one courier. Provider plug-ins attach external transport to the same INPROFIC delivery record.

Glovo LaaS v2 is available as a built-in optional provider. A tenant enters its own Glovo onboarding credentials and pickup Address Book ID. INPROFIC can use Glovo live quotes, create the parcel after verified payment, store tracking references and synchronize delivery status through webhooks.

The tenant remains free to use in-house delivery, Glovo, a manual external courier or a future provider without changing the core Commerce workflow.

### True Hybrid delivery

Hybrid means both in-house and the configured provider remain available methods rather than one being merely an emergency fallback.

The tenant can choose policies such as customer choice, dispatcher choice, lowest fee, fastest ETA, in-house first or provider first. The tenant also controls what may happen if the delivery method changes after payment.

The customer-facing switch policy is displayed before payment. INPROFIC never silently adds a higher delivery charge after the customer has already paid.

## Delivery notifications

Delivery uses the durable Commerce notification system so important actions are not dependent on someone repeatedly refreshing a page.

Notifications can cover assignments, reassignments, delivery-status changes, rider issues, issue responses, provider failures and method switches. Assigned riders can receive targeted notifications, while staff with the appropriate Commerce/Delivery permissions receive the broader operational alerts they are meant to handle.

Where supported by the deployment and browser, notification delivery can include live updates and Web Push.

## Storefront customer profiles

Customer registration is optional. A shopper can still browse, check out and track a delivery without creating an account.

A customer who chooses to register gains a tenant-scoped purchase profile with saved contact/address information and checkout history. Customer accounts are separate from INPROFIC staff users and cannot be used to cross between tenant businesses.

A fully headless external website may continue to own its own customer login system instead; it sends the current customer snapshot to INPROFIC during checkout and stores the returned checkout/order identifiers against its own customer record.

## Finance and cash truth

Finance separates commercial performance from cash movement.

Sales revenue, production cost, receivables, payables and cash transactions are related but not treated as the same thing. Receiving money records the actual settlement; owing money is not represented as cash; and paying a supplier reduces the payable through a real payment event.

This provides the foundation for revenue, cost of goods sold, gross profit, outstanding customer balances, supplier balances and cash-flow reporting.

## Audit Workspace

Audit Workspace is a separate plan-gated surface designed for external or internal audit review.

An External Auditor does not need ordinary Inventory, Procurement, Production, Sales or Finance navigation permissions. Instead, Audit Workspace reads the relevant tenant-scoped evidence and presents it through a specialized, read-only review environment.

The workspace can bring together materials, procurement, inventory, products, costing, production evidence, sales, payments, expenses, finance transactions, measurement changes, delivery records and the general audit trail.

Auditors can raise queries or flags against specific evidence records without changing those records. Admins, accountants, managers or other users can review/respond only when they have been deliberately granted the appropriate Audit Workspace reviewer/edit access.

## Roles and access

INPROFIC combines plan entitlements, roles and optional per-user permissions.

Examples include Business Admin, operational roles, POS Operator, Delivery Rider and External Auditor. Purpose-specific workspaces keep narrow roles focused: a rider does not need Finance, a POS-only cashier does not need the main dashboard, and an external auditor does not need the business's normal operational forms.

Actions that alter protected business data still require the corresponding effective permission inside a module that the tenant plan has enabled.

## Subscription and founder controls

Founder-level subscription management controls plan pricing, billing periods, module entitlements and commercial access ceilings.

Modules such as Delivery and Audit Workspace can therefore be switched on only for the plans that should include them. Tenant records are not deleted simply because entitlement changes; access and new activity are controlled while historical evidence remains intact.

## Reporting, exports and backup

INPROFIC provides operational reporting and export paths for core business records. Audit Workspace can export evidence for review, while the wider platform retains stock, procurement, production, sales and finance histories.

Backups and deployment storage should preserve both database records and uploaded media. Historical commercial and costing snapshots are intentionally not recalculated every time a current setup value changes.

## Notifications and operational attention

Commerce and Delivery use durable notifications so staff can see work that needs attention. Notification records remain available even when a browser was not open at the exact moment an event occurred.

The platform is designed so payment, delivery and fulfilment state changes are explicit rather than hidden inside a generic order status.

## What INPROFIC intentionally keeps separate

Several distinctions are deliberate because combining them would weaken business truth:

- a Product Category is not the same thing as whether a product is manufactured or purchased for resale;
- a paid checkout is not the same thing as a delivered order;
- delivery provider status is not the same thing as stock or finance status;
- receivable/payable balances are not the same thing as cash movement;
- a current material measurement setup does not rewrite completed historical movements;
- an auditor query does not change the evidence being queried; and
- a customer storefront account is not a staff user account.

## Questions INPROFIC is intended to answer

A well-configured tenant should be able to answer questions such as:

- What did we buy, from whom, at what historical cost, and have we paid for it?
- What raw materials and finished products do we currently have?
- Which products are made in-house and which are purchased for resale?
- What can we make from the available materials?
- What did we actually make, waste and accept as saleable output?
- What did that production batch cost using the material prices available at the time?
- Which customer ordered or bought each product and through which sales channel?
- What do customers owe us and what do we owe suppliers?
- Which payments actually reached or left our settlement accounts?
- Which deliveries are awaiting dispatch, with a rider, with Glovo, delayed, completed or disputed?
- Was a destination inside the configured delivery radius and what precise base-to-customer distance was priced?
- What evidence changed after a measurement conversion and what historical evidence remained frozen?
- What questions has an auditor raised and who has responded?
- Which modules should this tenant's current plan allow?

## Current scope and future growth

INPROFIC has a strong connected foundation across inventory, procurement, production, commerce, delivery, sales, finance and audit. It should not be represented as a fully exhaustive manufacturing ERP.

Advanced planning areas such as full MRP, WIP scheduling, comprehensive labour/overhead costing and formal QC quarantine/release workflows remain opportunities for future expansion. The existing architecture is intended to let those capabilities grow without sacrificing the transactional and tenant boundaries already in place.

## The short description

**INPROFIC is a production-aware, commerce-connected business operations platform that links procurement, inventory, manufacturing/preparation, sales, payments, delivery, finance and audit in one tenant-safe operating record.**
