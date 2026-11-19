# Retail branches and offline packages

Branches are locations of the same business. They inherit headquarters' vertical, subscription modules and report depth, and do not create additional subscription services. Founder settings can set a branch limit on every plan (blank means unlimited).

## Setup and daily use

Open **Operations → Branches & cashiers** at headquarters. Create a location, assign an active staff member as its manager and choose either:

- **Shared headquarters catalogue:** headquarters maintains products, materials, categories, recipes, portions, bundles, channel prices and storefront product definitions. Branch-local copies use stable IDs and follow master updates. Local balances, costs, production, sales, payments and history remain separate. Changing stock measurements requires an explicit branch conversion before further catalogue synchronization.
- **Independent branch catalogue:** the branch maintains its own definitions. Switching from shared to independent keeps its current catalogue. Switching back to shared requires resolving conflicting independent names or barcodes; conflicts never silently merge historical stock.

Each full branch is an operational workspace beneath headquarters. Its manager can manage branch staff and permitted modules. Headquarters administrators enter any branch using the workspace switcher or **Open branch workspace**. Deactivating a headquarters administrator's membership removes inherited branch access. Branch managers cannot enter headquarters or sibling branches without their own explicit membership. Plan changes and expiry apply from headquarters; billing remains managed there.

Older stock/POS locations retain their allocations and historical transfers. **Enable full workspace** requires selecting a manager and resolving in-transit or allocated stock first; existing history is retained. Headquarters can send stock to a full branch. A shared catalogue matches its receiving product automatically; an independent catalogue requires selecting a receiving product with the same stock unit. Receipt writes auditable stock movements in both workspaces, once; shortages require an explanation. Transfers between the original allocated locations retain their existing consolidated-stock semantics.

Business administrators can assign staff to branches and optionally require
approved devices and an open cashier session. Staff register this browser from
**My profile**; administrators approve or revoke it in Device access. A revoked
device cannot prepare or sync offline packages. Offline access cannot receive
revocation until it reconnects. Profiles support personal/contact details and a
photo, including the phone's camera when supported. Select **Remove profile picture** and save to clear it without uploading a replacement; the preview returns to the placeholder. My profile is a standalone
editable page, with personal details and device registration alongside the photo.

Cashiers open a drawer from POS or the branch workspace. The opening form identifies the business, branch and cashier and records the cash float already in the drawer. POS then links to **View drawer** and **Count & close**. A cashier can have one open drawer per business workspace and must close it before selling in a different allocated location.

The drawer detail shows opening float, verified cash receipts, expected cash, receipts by payment method and payments still awaiting resolution. Resolve pending payments and sync prepared offline receipts before closing. Enter counted cash and a variance explanation when it differs from expected cash. The branch manager or headquarters administrator can review the closed session. The expected amount captured at closing remains historical even if a receipt is subsequently reversed.

Cashiers record opening cash and closing counts. Expected cash uses verified,
unreversed cash receipts, with an explanation for discrepancies and manager
review. Mixed tender checkout supports cash plus a staff-verified bank transfer.
It never treats a transfer claim as verified electronic payment. Closing cash
counts do not silently adjust Finance balances.

Products can have optional barcodes; POS supports keyboard scanners and camera
scanning where the browser supports it. Product CSV import previews validated
names, units, prices, stock and barcodes before confirmation; opening stock uses
the audited stock service. It creates new products and does not overwrite them.
Parked POS baskets can be saved and resumed. Browser printing supports receipts;
physical printer, Bluetooth and drawer compatibility require separate testing.

## Customer quotations and product promotions

**Sales & Finance → Customer quotations** shares a priced customer request for
acceptance before payment. Acceptance alone does not create a sale, release stock
or record money. Authorized staff convert an accepted, valid quotation to an
unpaid checkout at its agreed prices. The normal verified-payment boundary then
creates the order. This complements storefront ordering rather than replacing it.
Add products with **Add new product** and remove a row with its **Delete** button.
The shared quotation and staff preview use the storefront receipt document,
including the business logo, contact details, line prices and quoted total.
Quotation status stays distinct from payment confirmation.
Selecting a product or changing the channel fills its registered channel price
(using the normal default-price fallback when no channel price exists). Staff can
adjust it before saving. Each line preserves the channel price at issue and the
agreed price; staff see an internal price record, and the audit captures adjustments
with the issuing user. These records stay off the customer document. Existing
quotations without a baseline show it as not recorded; later master-price edits
do not rewrite the saved baseline or quoted price.

**Product promotions** offers scheduled buy/get and mix-and-match product sets.
Buy/get campaigns select qualifying **X products** and separate **Y products**
to receive free. A reward unit cannot also count as a purchased qualifying unit;
a separate free reward is automatically added from the available Y choices, cheapest first. Customer-added Y quantities remain paid. Existing same-product campaigns
retain their previous reward set until edited.
Offers on distinct qualifying quantities can apply together; overlapping offers cannot reuse the same paid quantity. Promotions do
not rewrite master prices or existing discounts. They apply to whole quantities of the selected registered
selling portions in their configured channels, including standard portions, bulk
packs and individual sale options. POS review uses the server's payable total before checkout.
Cent allocation may split a product into two price lines to preserve the exact
campaign total. Quotations retain their agreed prices.

## Offline preparation

While signed in, open **My profile → Prepare offline access**, enter a private
passphrase, and choose **Prepare / refresh packages**. Staff need an approved registered
device; administrators retain recovery access. Use the global Offline mode switch
to show only the permitted packages. Prepare the device before losing connection.
The existing connection-unavailable card opens automatically on connection loss,
on an unreachable server, or when opening a cached page offline. It keeps the
logo, retry action and light/dark appearance, with the global Offline mode switch
below. There is no sidebar or POS connection button.

The browser must support service workers, Web Crypto, IndexedDB and Web Locks;
HTTPS is required outside localhost. Unlock packages again after reopening.

The offline passphrase is chosen during preparation and protects locally saved
business records and unsynced receipts. It is separate from the login password
and is not sent to the server. A separate passphrase is a security choice of
this implementation, rather than a requirement inherent to offline operation.
After preparation, **Open offline workspace** is available immediately. On a new
page, enter the same passphrase and press **Unlock saved packages** or Enter.
Both package actions stay visible; unavailable actions are disabled with an
explanation. An incorrect passphrase leaves the unlock action available to retry.

The public offline shell and static assets are cached by the service worker.
Prepared screens use the same live templates, shared sidebar, theme and table
styles. Navigation contains only the prepared packages; unprepared actions are
unavailable. The POS uses its live cart, review and receipt flow with offline
cash handling. Uploaded business logos and product images within package size
limits are encrypted with the screens. Old packages must be refreshed while
online to receive the new interface; their existing queued work remains recoverable.

Business packages, queued operations and receipts are encrypted in IndexedDB with
AES-GCM and a passphrase-derived key. The passphrase is not stored. Losing it or
clearing browser storage loses unsynced work. One tab at a time can edit packages.

| Package | Offline access |
| --- | --- |
| Basic dashboard | Saved daily sales collections, paid-sales revenue and profit, according to the user's existing permissions. Totals are labeled as prepared data; pending offline sales are counted separately. |
| Operations: stock reference | Prepared Inventory page and balances, plus stock-review notes. Historical drilldowns, filters and mutations other than notes require connection. |
| Operations: expiration check | Prepared active Market Stock lots, including blank/expired dates; queue advisory review flags. Expiry does not block release or automatically write stock off. |
| Commerce: POS / Sales | Complete cash sales from the last prepared branch stock and prices, decrement local availability, save a local receipt and print it. Further sales cannot consume the same stock on this device. |
| Commerce: Delivery / Dispatch | View prepared pending, ready or assigned unbatched orders and active riders; assign an order to a prepared rider. Provider booking, pickup, delivery confirmation and rider batches require connection. |

Offline POS uses standard product quantities/prices; electronic payment
verification, bulk/individual options, delivery quotes and campaign evaluation
require connection. Current campaign or price differences are reported as sync
conflicts rather than silently changing a cash receipt. Prepared stock is not an
exclusive reservation across devices: concurrent live sales can cause conflicts.

## Confirmed synchronization

Connection returning only changes the connection indicator. It never posts
queued operations automatically. Choose **Review & confirm sync**, review the
business/branch and entry count, then **Confirm sync**. The server checks current
identity, device access, module permissions, branch, cash session, stock, prices,
promotions, ledger date and delivery version. Original offline sale time is
preserved. Each operation has an immutable UUID; retrying an acknowledged entry
does not create a second sale, receipt or assignment.

Only acknowledged entries leave the queue. Local receipts remain available and
show their live receipt reference after synchronization. Conflicts stay pending
with a review reason; resolve the live stock/permission/order issue before retrying.
Price or campaign conflicts require review of the actual cash receipt; there is
no automatic repricing, stock override, refund or finance adjustment. A locally
completed receipt remains pending until the server accepts it.

## Deployment

Run `python manage.py migrate` before serving the new code, then rebuild/collect
static assets using the normal release process. New additive migrations cover
branch plan limits, product barcodes, retail records and checkout branch/promotion/
offline-time snapshots, plus the separate promotion reward-product selection. Update `PWA_BUILD_VERSION` (or deploy with a new
`RENDER_GIT_COMMIT`) so installed clients refresh their static cache. No live
production database was migrated during development verification.

Offline navigation uses the shared sidebar buttons without accordion groups. Inventory displays both prepared product and supply tables; import and other unavailable links are removed. Refresh saved packages to capture the updated navigation.

## Quotations, names and barcode checkout

Quotation titles are entered by staff. The title appears on lists and on the shared storefront-style quotation document, alongside a stable Q reference. Existing quotations without a title keep the generic document heading. Acceptance and conversion retain their existing no-payment/no-reservation boundary.

Business Settings → **Module & menu names** stores overrides per business vertical. Navigation, registered workspace labels, module badges and executive dashboard labels use the same vocabulary. Blank values retain vertical defaults. Changing names never changes URLs, model keys, permissions or entitlements. Headquarters defaults are inherited by branches, with local overrides available. Refresh prepared offline packages to capture changed labels.

Save an existing manufacturer code or a unique internal product barcode. **Barcode label** in the stock workspace renders a printable Code 128 SVG. Print the label and attach it to the item or shelf. POS can use a keyboard scanner, manual product-code search, or browser-supported camera scanning. Scanner characters must match the saved product barcode.

**Park basket** saves the cashier's unfinished basket for the current branch, then clears the cart. **Parked baskets** lists its customer/name, saved time and product count in the shared dialog/table layout. Restore requires an empty active cart; prices and availability are checked again before sale. Parking does not reserve stock or record money.

### Promotion ribbons and offline POS

POS, hosted storefronts and the headless product API share offer metadata. Offers
appear on the top left of qualifying/reward cards, with one help tip listing all
applicable offers for the current channel. Terms use the shared help-tip heading,
body and list typography. Optional Featured, New arrival and Staff pick badges
appear on the right; otherwise recent paid commerce sales determine Bestseller.
Product Commerce publishing settings control the optional badge.

Offline preparations include eligible physical-store campaigns and the business
timezone. Offline POS prices standard whole-unit buy/get and bundles in cents,
keeps the receipt's discounted line snapshot and decrements every selected unit,
including free rewards. Reconnect still requires explicit sync confirmation.
Live prices, promotions and stock are revalidated; a changed payable amount is
rejected for review instead of silently charging a different total. Older packages
without campaign metadata retain their previous behavior; refresh packages to
capture these offers. No unsupported module is added to offline navigation.

### Multiple promotion channels and scheduled prices

Select All channels, or several explicit channels. Selecting Buy X products
populates each product's current channel prices, including multiple products.
Edit the prices independently; they apply only while the campaign is active in
those channels. Master prices remain unchanged and expiry/pausing restores them.
Price changes are audited with the saved campaign. Ordinary fractional quantities
can use scheduled unit prices; whole quantities still qualify for buy/get and
bundles. Prices may increase or decrease, with nonnegative displayed savings.

Offline snapshots retain ordinary prices for live validation plus each campaign's
scheduled prices. The offline cart, review and receipt use those prices before
reward allocation. Reconnection still requires confirmation; a changed master
price, campaign or payable amount requires review. Refresh prepared packages after
editing campaigns to capture the new configuration.


### Promotion modes and selling portions

Choose a promotion **Mode**: Buy X/get Y free or Mix-and-match bundle. Mix-and-match
counts selected X selling portions towards the bundle quantity and price; it does
not use a Y reward. Each X and Y product has its own portion selector, using its
standard portion, active bulk packs or registered individual sale options.
A standard portion may already be a pack. Register a separate individual sale
option to offer units independently, with its own conversion and channel prices.
Prices auto-fill for the selected X portion and remain editable for the campaign.
Only matching portions qualify; rewards retain their full stock demand.

The help tip keeps the promotion name as its title. Eligibility rule and Pricing
rule are optional wording fields: blank fields are omitted entirely. The date
line contains only the scheduled dates, without channels. Earned rewards appear
automatically as separate free basket lines; customer-added Y quantities stay paid. Checkout freezes these terms for historical records.
Offline cash POS keeps its prepared standard-portion scope: unsupported pack or
individual selections never qualify as ordinary units. Refresh packages after edits.


Automatic reward quantities are included in live stock reservations and fulfilment.
Offline POS deducts both paid and free quantities from its prepared availability;
confirmed sync recomputes the reward and requires review if it has changed.


Scheduled prices resolve per qualifying product and selling portion, so another
product’s offer cannot restore its original channel price at checkout or receipt.
Separate qualifying products can all earn the same Y reward; earned quantities
accumulate while combined paid/free demand stays within available stock. The same
paid quantities cannot earn overlapping rewards twice. Prepared offline POS uses
the same rules; refresh packages to receive the updated calculation.
