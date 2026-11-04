# Ledger Cut-over, Backdated Entry and the Complete Dashboard

## What it does

A business admin picks a **Live start date (D)**. Records dated before D form the
**Backlog**: hidden from normal screens, reports and dashboards, but still
enterable (in *Backlog entry mode*) up to the day before D. Live restarts from
**opening positions** (counted stock and cash) at D. Nothing is copied or
deleted; a record's segment is a pure function of its business date.
An optional **merge** makes Live continue from the backlog's closing position
"as though there was no break", and can be undone.

Setup page: **Ledger setup** (`/ledger/`, business admin only).

## States

| Status | Backlog entry | Notes |
|---|---|---|
| `open` | allowed (needs `ledger` edit or admin) | default after starting |
| `complete` | locked | merge preview/commit available; can be reopened |
| `merged` | sealed | Live opening = backlog closing; undo is available |
| `archived` | sealed | kept separate, viewable read-only in Backlog mode |
| `reverted` | n/a | split removed; one combined ledger again |

`revert_split` is available for 7 days while `open`/`complete`; `dissolve`
removes a merged split at any time. Only one non-reverted cut-over exists per
business.

## Rules that keep the periods apart

* **Scope**: Live (default) / Backlog / Combined. Models opt in with
  `ledger_date_field`; `BusinessManager` filters automatically. `raw_objects`
  and `all_segments()` are the unscoped escape hatches. Combined and Backlog
  views need `ledger` view access (Backlog *entry* needs edit).
* **Entry guard**: `pre_save` rejects a *new* record or a *changed date* on the
  wrong side of D with a friendly page (and a one-click switch when the backlog
  is open and the user may enter it). Updating an existing record without
  changing its date is always allowed, so a Live payment can settle a Backlog
  invoice.
* **Stock**: Backlog-dated movements never change stored stock. Items with a live
  opening position show *opening + live movements* (Backlog mode shows the
  backlog balance); items created after D keep their stored stock.
  `rebuild_live_stock` repairs drift and re-stamps `balance_after`.
* **Receivables / payables** stay invoice-level across both periods.
* **Costs** (`RawMaterialCostSnapshot`) and the storefront queue are *not*
  scoped: they are reference data / a real-time queue.
* **Cash**: `CashAccount.balance` and `ledger.apply_cash_totals` follow the scope.

## Backdating

Approve, complete and receive accept an optional `event_date` (today by default;
the document date in Backlog mode). It may not be in the future nor earlier than
the document, and must be valid for the current scope. A late-entered older
receipt never overwrites a newer dated cost.

## Merge and undo

Merge replaces each live opening with the backlog closing and re-bases cash.
Undo restores the openings and rebuilds stock from restored openings plus *all*
live movements, so activity recorded after the merge is kept. Costs and market
lots are restored only where nothing has changed them since the merge.

## Complete dashboard

`/dashboard/complete/`: Simple / Complete switch, group dropdown, section tabs.
Each group starts with a summary section (KPIs, charts, breakdowns) followed by
record lists. Sections load on demand, are module-permission filtered, respect
the ledger scope and a period selector, and fail independently. Available to
business admins, managers and MD/Directors.
The Complete dashboard does not duplicate the ledger-mode dropdown. Business
admins use Ledger setup for scope controls. The shared scrolling ledger status
is admin-only; permitted non-admin users keep their separate mode-switch actions,
including Return to Live when viewing Backlog or Combined data.

## Recipe ratios

`inventory/recipe_ratios.py` derives "1 kg per 20 kg Flour" (and the share where
units are comparable) from the stored per-batch quantities and the product's
base material. Shown in dashboard search detail, the inventory item modal, and
the Complete dashboard's *Recipes & Ratios* section. No stored data changed.

## Market stock lots

Lots are a stock pool, not dated history. A lot received before D that still
holds stock stays visible and sellable in Live. Only lots created *while entering
the Backlog* (`MarketStockLot.is_backlog_entry`) are hidden from Live, so backlog
entries never add sellable Live stock. Merge sets each pre-D lot to the combined
balance of all its movements and makes backlog-entry lots live; undo restores
them where nothing changed since.

## Base-material sizing and the pool ratio check

Per 20 kg flour a recipe may use 1 kg sugar and 240 g salt. Product-quantity
sizing and legacy line-level base sizing keep that ratio. In an order-level
base-material *pool* the whole pool is released as the base ingredient while other
ingredients follow the ordered batches (a deliberate rule). When the pool differs
from what the ordered batches need by more than 2%, the order page shows an
"Ingredient ratio check" with what is being released versus what the full pool
would need at the recipe ratio. Consumption itself is unchanged.

## Known limits

* Recipe quantities are stored to two decimals, so very small ratios round
  (only relevant for tiny quantities in kg or litres; use g or ml for those).
* Stored `stock` aggregates (e.g. SQL `Sum("stock")`) are live figures even in
  Backlog mode; per-item reads show the backlog balance.
