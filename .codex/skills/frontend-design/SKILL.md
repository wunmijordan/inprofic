---
name: frontend-design
description: >
  Design and implement polished StoreTrack Django template/UI work while preserving
  existing form, JavaScript, permission, responsive, branding, and workflow behavior.
  Use for templates, modals, dashboards, forms, tables, navigation, print views, and UI refactors.
version: "3.2"
updated: 2026-10-29
---

# Frontend Design — INPROFIC

Use this skill whenever the task changes HTML templates, CSS/Tailwind classes, JavaScript-driven formsets, dashboards, modals, tables, navigation, exports/print surfaces, or the visual presentation of a StoreTrack workflow.

## Primary objective

Improve usability and visual quality **without changing business semantics accidentally**. StoreTrack templates are not decorative shells: many of them carry formset prefixes, hidden management fields, pricing resolvers, channel-specific visibility, stock calculations, confirmation flows, and permission-aware actions.

## Design review skills

For substantial design work, read `impeccable/SKILL.md` and the relevant Operate,
polish or craft reference. Use `web-design-guidelines/SKILL.md` for the final
interaction/accessibility review and `playwright-cli/SKILL.md` for browser
verification. These official project-installed skills supplement this contract;
the user's explicit brief, tenant safety and form/domain invariants prevail.
Read `PRODUCT.md` and `DESIGN.md` for the established context. Avoid decorative
eyebrows, interchangeable card grids, fake metrics and generic promotional copy.
The requested subtle button glass is an intentional exception limited to actual
visual buttons; never spread it to clickable surfaces.

## Shared design contract

Read the actual shared assets before implementation. They are the source of truth;
do not reconstruct the design from older templates or introduce a page-specific palette.

- `apps/core/static/core/css/workspace.css`: workspace surfaces, text, cards,
  controls, tables, semantic states, elevation and light/dark theme tokens.
- `apps/core/static/core/css/button-surfaces.css`: subtle shared glass finish
  for buttons and action links, including standalone auth, marketing and storefronts.
  Load after workspace styles; do not recreate the finish in individual templates.
- `apps/core/static/core/css/form-experience.css` and
  `apps/core/static/core/js/form-experience.js`: field enhancement, validation,
  password controls, dynamic rows and sticky tables.
- `apps/core/static/core/js/workspace.js`: theme/palette preferences and derived
  surface contrast. Check `templates/base.html` for asset order and shared wiring.
- `apps/core/static/core/js/brand-assets.js`: switches marked logo artwork against
  its actual composited background after theme, palette and dialog changes.
  Mark brand wordmarks with both `data-brand-logo-light` and
  `data-brand-logo-dark`; never select them only from the saved tenant setting or
  theme name. Check desktop/mobile navigation, auth, install prompts and public
  header/footer placements. Customer-uploaded logos are not replaced.
- `assets/css/tailwind.css`: utility source; rebuild the committed
  `apps/core/static/core/css/inprofic.css` with `npm run build:css` after utility changes.

Use `--ui-bg`, `--ui-surface`, `--ui-surface-muted`, `--ui-border`,
`--ui-text`, `--ui-muted`, `--ui-surface-text`, `--ui-surface-muted-text`,
the shared radius and shadow tokens, and local surface text inheritance.
Cards and forms must use the shared surfaces, spacing and elevation in both themes.
General text, headings, links and outline actions use neutral theme text, never
the brand/tenant background or accent as their foreground. Tenant colors may fill
primary actions, with `--ui-accent-contrast` for their labels, and mark focus/selection.
Semantic success/warning/error colors remain meaningful and need matching contrast.

Ordinary tables share neutral theme surfaces, headers, borders, hover and pinned
cells globally. Status badges may convey state without recoloring entire ordinary
rows. Inventory is the explicit exception: `inventory-low-row` and
`inventory-warning-row` keep their stock-state backgrounds, with text, links,
controls and sticky cells adapting to that same background in light/dark and hover.
Do not neutralize stock states or leave a pinned column on the previous theme.
Preserve scroll containers, sticky headers/first columns and `data-no-sticky` opt-outs.
Fix shared behavior in shared assets; avoid copying CSS into each page or adding
another cascade of conflicting overrides. Public storefronts and print/export
surfaces keep their existing intentional context and contrast.

## Operational presentation

### Marketing content preservation

Before refining marketing, inventory the current copy, FAQs, plan/promotion rules
and interactive behaviors. Preserve them unless the user explicitly asks to remove
them. INPROFIC's typing headline, logo-connected module explorer, service-specific
walkthroughs, storefront/POS demonstration and full FAQ are intentional product
content. Keep them functional, theme-aware and accessible; do not replace them
with a shorter generic landing page. Label demo data. The public walkthroughs
autoplay while visible, pause for interaction and respect reduced motion; the
user explicitly requests no playback controls. Keep the stock-to-sale example
synchronized with the selected service. The typing headline and its SVG retain
the explicitly requested flowing brand gradient, with legible theme-specific
stops and a shared baseline with “Run”. Compare concrete workflows anonymously; do not
name competing products or imply unsupported capability gaps.
The live public site is the visual reference: preserve its Fraunces display
headings, Sora body copy, solid contrasting cards, section scroll entrances and
staggered module-list glide. Do not flatten these into transparent wrappers or
replace scroll entrances with barely visible one-off card animations. The typing
SVG has no background and occupies its own stable layout slot, including while
the word is empty; test the whole typing/deletion cycle.
The latest public brief restores the live site's own navy hero, grid and ambient
backgrounds, cream canvas, white cards with original shadows, overlapping bridge,
and green WhatsApp contact bubble. This authored public palette remains stable
when workspace theme preferences change; scope surface/text/border roles locally
so shared controls stay readable. Do not reintroduce inverted cards or flatten
the live card shadows. Preserve the original module SVG colors. Hero and module
cards float gently, pause on hover/focus, and stop under reduced motion. Keep
Shared Run/stock-to-sale examples and workflow comparisons inside “See the
connected controls”; the plan matrix lists the capabilities without another
comparison card beneath it. The public content width is 1320px plus outer gutters.
The public dark theme uses brand navy (`#050733`) and the user's approved
sectional gradients. Preserve this intentional treatment while keeping light
mode neutral, text legible and interactive surfaces on the shared theme roles.

Use Tailwind and the compact Tabler visual language for layout, typography,
tables and navigation. Use the owned shadcn/Radix sources in `assets/ui` for
dialogs, confirmations, popovers, dropdown menus and searchable selections.
Build with `npm run build`; the committed bundle serves Django pages without
a Node server. Do not add another independent interaction library.

Design around real work: useful column order, clear record names, restrained
spacing and one obvious next action. Avoid repeated feature-card grids, floating
orbs, decorative gradients, mock revenue counters, spinning diagrams, or generic
AI/SaaS copy. Marketing should demonstrate actual stock-to-sale workflows with
honest limitations and current plan availability.

- Prefer restrained hierarchy: clear headings, compact operational tables, legible status badges, and obvious primary actions.
- Keep finance/stock status colors semantically consistent:
  - green = sufficient/success/cleared;
  - amber = warning/attention;
  - red = low/error/destructive.
- Do not add decorative complexity that makes production-floor or finance tasks slower.
- Make forms usable on desktop and narrow screens; preserve horizontal table usability where dense operational data genuinely needs it.

## Buttons

- Use the shared shallow highlight and inset edge. Keep labels sharp and the
  centre of the fill clear; avoid backdrop blur, large glows or animated shine.
- Glass belongs only to existing visual buttons. Mark those with
  `data-button-surface` or the shared button classes. Never infer it from
  clickability, an anchor/role, rounded corners, a border, a background or a click
  handler. Navigation, clickable cards/rows, badges, text links, tabs and input
  widgets keep their own presentation. Include negative browser assertions for
  these surfaces when changing the shared button rule.
- Primary action fills follow the selected brand/tenant accent and its contrast
  token. Use `workspace-action workspace-action-primary` in the workspace or
  `ui-button ui-button-primary` on a standalone surface. Secondary workspace
  actions use a light accent tint with neutral theme text.
- Preserve semantic warning/delete colors, disabled states and keyboard focus
  rings. Use the shared hover edge instead of brightness filters that reduce
  label contrast. Tabs, switches, color samples and modal backdrops keep their own state
  presentation; use `data-no-glass` for another intentionally bare control.
- Verify the actual label region in light/dark themes, including pale and
  mid-tone tenant accents, hover, dynamic buttons and Inventory stock-state rows.
- Keep the finish decorative: no changes to form submission, permissions or
  click handlers. Forced-color and print views suppress the glass layers.

## Before editing a template

1. Read the corresponding view and form/formset.
2. Identify every POST field, management-form field, prefix, data attribute, and JavaScript selector the template depends on.
3. Search for shared JavaScript in `templates/base.html` before adding another formset-cloning implementation.
4. Check whether the page is business-scoped or permission-sensitive.
5. If a modal is populated from JSON, inspect both the JSON producer and consumer before renaming attributes or keys.
6. For new pages, connect the shared desktop/mobile navigation and parent workspace
   actions. Align visibility, active states and server permission checks; an existing
   URL alone does not make a feature discoverable. Personal preferences must not
   require permission to edit operational data.

## Dynamic formset rules

StoreTrack uses Django formsets heavily. When adding/removing rows:

- preserve the correct `<prefix>-TOTAL_FORMS` management field;
- never assume a prefix is `form`;
- replace every `__prefix__` occurrence in `name`, `id`, `for`, and relevant data attributes;
- clear database IDs and DELETE flags on clones;
- reset values to the intended domain defaults (often `0`, not blank, for numeric Order fields);
- remove stale JS "already wired" markers from clones;
- re-run product/price/channel wiring on the new row;
- surface field and non-field errors visibly after a failed POST.

For production Order rows, confirm newly-added rows retain:

- product price auto-resolution;
- ordered batches/pieces defaults;
- planned-production batches/pieces defaults;
- discount defaults;
- channel/customer context.

## Forms and numeric precision

- Match HTML `step`, Django field precision, model precision, and displayed initial values.
- Do not format a valid Decimal into a browser-invalid value.
- Quantities and money are different domains; never change currency precision merely because a stock field needs more precision.
- Use normal HTML escaping inside HTML attributes. Use `escapejs` only inside actual JavaScript strings; otherwise values such as `2-in-1 Bread` can surface as literal escape sequences.

## Operational tables

- Keep the most decision-relevant columns visible first.
- Add derived columns only when their meaning is defensible from current data.
- Label estimates/traceable pools conservatively when the ledger cannot provide true lot-level precision.
- If adding export actions, keep them adjacent to the table title/filter context and ensure exports use the same business scope and status logic as the screen.

## Modals

- Routine create/edit links use the shared form dialog allowlist in
  `assets/ui/workspace.tsx`. Keep existing Django forms, CSRF, validation and
  formset scripts as the save boundary; direct full-page URLs remain valid.
- Keep approval, stock release, completion, reversal and cut-over review on their
  deliberate review screens. Use dialogs for contained edits, not to hide a
  complex decision or nest several workflows.
- Preserve dirty-form confirmation, Escape, keyboard focus containment, return
  focus, visible server errors and a full-page escape route. Test successful and
  failed saves before adding a route to the dialog allowlist.
- Enhanced selections keep the original native select as the POST source, with
  its name, required/disabled states, option values, change events and reset
  behavior intact. Observe option changes and `ms-refresh`; clean cloned widget
  markup when new Django formset rows are inserted.

- A modal should still communicate useful master/configuration information when transaction history is empty.
- Separate master data (recipe, configured inputs, thresholds) from history (movements, batches) visually.
- Avoid hiding an empty-history modal entirely if the user still needs the configured recipe/input information.

## Destructive actions

- Use visually distinct destructive styling and explicit confirmation text.
- Do not make deletion look equivalent to edit/view.
- Reversal and deletion are different operations; UI language must reflect that distinction.

## Validation pass

For UI verification, read `.codex/skills/playwright-cli/SKILL.md` (official
Playwright CLI skill, synced from the pinned npm package). Use `npm run test:ui`
for the repeatable Chromium suite; it creates an isolated local test database.
Keep its coverage of dialogs, invalid saves, selections, confirmations, mobile,
themes, contrast and both feature matrices. Use `npm run ui:inspect` for guided
Chrome exploration. Never run write tests against a live business database.

Before finishing template work:

- check Django template control blocks are balanced;
- inspect all changed formset management fields and prefixes;
- search for referenced IDs/classes/data attributes in JS;
- test empty, one-row, and multiple-row states mentally or in-browser when available;
- verify failed POST errors are visible;
- verify mobile/narrow layout does not hide the primary action;
- ensure no tenant/permission action became visible to the wrong role.
- verify shared navigation and parent-page entry points for every new surface;
- check light/dark themes and different tenant palettes, including cards, labels,
  table hover, pinned cells, empty states and Inventory warning/low rows;
- keep ordinary foreground text neutral and primary-action labels contrasted;
- measure normal and muted text against the actual composited surface (aim for
  at least 4.5:1 for ordinary text). Include inactive tabs, hover states,
  translucent utility backgrounds and light tenant palettes in dark mode;
- use shared assets for reusable behavior and rebuild CSS after utility changes.

After implementation, also apply the `simplify` and relevant domain-integrity skill.

## Form dialog finish

Use one outer dialog surface: flatten enclosing page panels in embedded forms,
while keeping meaningful interior field groups. Do not add an “Open full page”
action to form dialogs. Use the shared short opening/closing animations and a
subtle 2px backdrop blur; respect reduced motion. Approval reviews remain
dedicated review surfaces, separate from editing the underlying request.
