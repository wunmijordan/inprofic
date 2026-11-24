# Shared workspace forms and navigation

Authenticated workspace form links open in the shared dialog. Form content sits
directly in its body, with one title and close control in the header. Existing
Django validation, CSRF protection, permissions, formsets and review steps remain
responsible for submission.

`assets/ui/workspace.tsx` classifies operational form routes across modules.
Use `data-form-dialog` on an additional form link or `data-no-dialog` on an entry
point that needs ordinary navigation. Normal links retain their original URLs for
direct visits and modified clicks. Inline POST editors use their existing form
nodes and listeners. Order approval stays on the order detail page: its form uses
`data-no-dialog` so flexible material adjustments and the required approval date
remain visible. Shortage review retains the entered amounts and date; its explicit
“Approve anyway” button submits the same form, including any further adjustments.
Stock is released only after successful approval or explicit shortage clearance.

Dialog dimensions come from route profiles before the content loads. Long forms
use the full phone viewport; short forms retain a compact dialog. The embedded
request includes `ui_dialog=1`, omitting repeated navigation, alerts and install
prompts. Forms become visible at DOM readiness rather than waiting for unrelated
images. Changes to the profiles require rebuilding the committed UI bundle with
`npm run build:ui`.

The shared form-experience script places a readable weekday/date above native
date and local date-time inputs, including dynamic form rows. Native values,
time components, minimum/maximum constraints and submitted formats are retained.
Distribution return and customer payment choices show the sale's short date
beside its invoice identifier, for example `Sale #123 · 18 Nov 2026`.

Stock availability follows the selected release date. Eligible batches must have
arrived by that date and meet its expiry rules. Backlog availability reconstructs
movements through that date; Live availability uses current remaining balances
with the selected date's receipt/expiry filters. A backdated Live selection is
therefore not a complete historical stock rewind. Presentation changes do not
alter those stock calculations.

Both dashboards greet the authenticated user using the configured local time:
morning before 12:00, afternoon before 17:00, and evening thereafter. Navigation
links and accordion titles use shared SVG icons. The desktop navigation can
collapse to an icon rail with a single logo mark; its preference persists locally.
Permission conditions continue to control both expanded and collapsed menus.

Browser coverage lives in `tests/ui/frontend-consistency.spec.cjs`, alongside the
workspace and operating-panel checks. The optional
`INPROFIC_UI_ALL_MODULES_FIXTURE=1` flag enables additional modules and a flexible
production approval only in the isolated temporary UI fixture database.


Connection loss retains the centered INPROFIC offline card, with a working theme-
appropriate wordmark, retry action and the global Offline mode switch. It opens
automatically on browser disconnection or a failed server reachability check.
Offline preparation lives under My profile; no persistent connection button is
added to the sidebar or POS toolbar.

Workspace pages fill the available width with the sidebar expanded. Collapsing
the sidebar centers content in the remaining area, capped at 96rem. New retail
lists use the same rounded, theme-aware table containers as existing modules.

Founder inline editors retain their shared modal, with spacing around extracted
action buttons. Publish policy uses the wide modal profile. Opening a dialog
closes existing help tips and focuses the header; tips open only on interaction
and remain within the viewport.

My profile stays a standalone editable page. Quotation product rows use an explicit
Add new product action and Delete buttons, preserving Django formset identifiers
and validation. Quotation documents reuse the storefront receipt template and
print only the document. Prepared offline screens reuse the live page templates,
shared navigation and styles, with only packaged operations enabled.

Quotation product and channel changes fill the channel price. Manual edits remain
allowed and are identified beside the field; saving snapshots the channel price
and records adjustments internally without exposing them on the customer document.

Business Settings remains a standalone editor. Help tips use a viewport-positioned portal to escape scrolling modal panels. Retail tables share cell padding and navigation selection uses the themed left marker. Profile photo previews replace the placeholder before saving.

### Menu icon customisation

Edit the matching `icon` branch in `templates/_nav_icon.html` to replace its SVG paths. Each retail item has its own key. Icons keep the same definition on desktop, mobile and prepared offline pages.

Set a per-icon colour in workspace CSS, for example:

```css
.tenant-sidebar { --nav-icon-dashboard-color: #f2b84b; }
```

The fallback is `--nav-icon-color`, then the menu text colour. For one include, use `{% include "_nav_icon.html" with icon="dashboard" icon_color="#f2b84b" %}`. Optional `icon_viewbox`, `icon_fill`, `icon_stroke` and `icon_stroke_width` parameters support replacement SVGs. Individual SVG paths may also supply their own fill/stroke for multicolour artwork. Choose colours legible against the sidebar in both themes. Refresh offline packages after changing templates.

Module and menu names are configured in Business Settings and stored per vertical; shared vocabulary preserves permission and route keys. Parked baskets use the shared dialog and table treatment, with a single header close action. Cashier opening/counting use the form host, while the drawer record is a standalone review page tied to the branch and POS.

### Shared help tips and product ribbons

`_help_tip.html` supports optional `help_title` (Fraunces), body text (Sora),
`help_items` lists with Sora subheadings, and optional `item.value` in IBM Plex
Mono. Existing plain-text, composition and business-hours tips remain compatible.
Templates pass escaped text/structured items, never arbitrary HTML. Generated
field tips receive the same body font; portal placement and viewport-clamped
scrolling remain global. Trigger controls reference their tooltip for assistive
technology. Long unbroken values wrap within the bubble.

Commerce corner ribbons use the shared `commerce/_product_ribbons.html`,
`product-ribbons.css` and `product-ribbons.js`. Existing card dimensions, padding,
images and controls are preserved. Offer help uses structured campaign terms;
merchandising badges carry SVGs and explanatory help. Public variant switches
update the selected product's ribbons and bulk selections hide excluded offers.

Public storefront dialogs use the same opaque modal surface as operational
screens. Shared background layers provide fallback surface/canvas colors when a
public page does not define workspace theme variables. Development service-worker
cache versions follow shared asset changes, so CSS fixes replace stale cached
assets without disabling offline support.

Promotion forms retain the shared modal/body/field flow. Multiple channel and X
product selections populate editable price rows using current channel prices;
validation remains server-side and selected values survive validation errors.


Product offer ribbons remain visible when switching selling portions. The help
explains which portions qualify. For multiple active offers in the selected
channel, its title includes the count and each offer keeps its normal heading
and structured terms inside the same scrollable, viewport-clamped help tip.
