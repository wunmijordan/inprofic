---
name: INPROFIC
description: The existing theme-aware operational workspace and public site.
colors:
  action-default: "#d14900"
  action-contrast-default: "#fff"
  canvas-light: "#ffffff"
  surface-light: "#eceff3ee"
  surface-muted-light: "#dee3e9"
  text-light: "#182433"
  muted-light: "#566473"
  border-light: "#cbd5e1"
  canvas-dark-fallback: "#111827"
  surface-dark-fallback: "#182230"
  surface-muted-dark-fallback: "#202c3b"
  text-dark-fallback: "#e8edf3"
  muted-dark-fallback: "#9ba9b8"
  border-dark-fallback: "#344154"
  danger: "#be123c"
  public-light-canvas: "#faf6ef"
  public-light-card: "#fffdf8"
  public-dark-card: "#fffdf8"
  public-dark-canvas: "#050733"
  public-dark-surface: "#10133f"
  public-dark-surface-muted: "#1b204e"
  public-dark-text: "#f5f6ff"
  public-dark-muted: "#c5cbe2"
  public-dark-border: "#3b416e"
typography:
  public-display:
    fontFamily: "Fraunces, Georgia, serif"
    fontSize: "clamp(2.35rem, 4vw, 4rem)"
    fontWeight: 600
    lineHeight: 1.13
    letterSpacing: "-.035em"
  public-headline:
    fontFamily: "Fraunces, Georgia, serif"
    fontSize: "clamp(2rem, 3vw, 3rem)"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-.025em"
  public-title:
    fontFamily: "Sora, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    letterSpacing: "-.02em"
  public-body:
    fontFamily: "Sora, system-ui, sans-serif"
    fontSize: "15px"
    lineHeight: 1.65
  public-action:
    fontFamily: "Sora, system-ui, sans-serif"
    fontSize: ".85rem"
    fontWeight: 600
    lineHeight: 1.4
  workspace-body:
    fontFamily: "Sora, sans-serif"
    fontSize: ".875rem"
    lineHeight: 1.45
  workspace-action:
    fontFamily: "Sora, sans-serif"
    fontSize: ".875rem"
    fontWeight: 600
  data:
    fontFamily: "IBM Plex Mono, monospace"
rounded:
  control: ".6rem"
  card: ".9rem"
  large-card: "1.1rem"
  pill: "999px"
spacing:
  compact-gap: ".4rem"
  tool-gap: ".5rem"
  action-gap: ".75rem"
  section-padding: "1rem"
  panel-padding: "1.5rem"
components:
  workspace-button-primary:
    backgroundColor: "{colors.action-default}"
    textColor: "{colors.action-contrast-default}"
    typography: "{typography.workspace-action}"
    rounded: "{rounded.control}"
    padding: ".5rem .8rem"
  public-button:
    typography: "{typography.public-action}"
    rounded: "{rounded.control}"
    padding: ".7rem 1.05rem"
  workspace-card:
    backgroundColor: "{colors.surface-light}"
    textColor: "{colors.text-light}"
    rounded: "{rounded.card}"
  form-field:
    rounded: "{rounded.control}"
  tenant-chip:
    rounded: "{rounded.pill}"
---

# Design System: INPROFIC

## Overview

**Creative North Star: "The existing INPROFIC operational workspace"**

Continue the incumbent identity: compact Tabler-style operational hierarchy,
Tailwind layouts, the established wordmark and tenant palette. Repeated daily
work needs readable records, aligned quantities, explicit states and clear actions.
The public site uses Sora and more space around explanatory copy and illustrative
operations. Light mode uses the shared neutral surfaces. Its explicitly requested
site restores its authored live navy/cream palette and white cards, with original background elements and shadows.

This is a record of shipped code, not a new identity. Shared CSS under
`apps/core/static/core/css/` is the implementation authority. The frontmatter
extracts actual defaults and role values; it does not replace runtime CSS tokens.
`PRODUCT.md` supplies durable product context and the review brief records the
confirmed extension contract. Page-specific composition remains in the templates.

**Key Characteristics:**
- Neutral, theme-aware text and surfaces with tenant accent action fills.
- Compact workspace hierarchy and consistent operational tables.
- Sora body copy, controls and subheadings; Fraunces main headings; IBM Plex Mono figures across the public site and workspace. Paper snapshot exports retain their reference Inter typography.
- Public brand navy sectional gradients in dark mode; neutral light mode.
- Connected module graph and autoplaying, service-aware illustrative walkthroughs.
- Restrained shared elevation and shallow highlights confined to visual buttons.
- Focus-managed contained edits that preserve Django forms and validation.

## Colors

The palette is neutral around records, with a configurable tenant accent for action.

### Primary

**Action default** is the fallback for `--ui-accent`, which resolves through
`--tenant-button`; its paired contrast token supplies the label color. These are
runtime values, not fixed orange/white requirements. Theme and palette controls
in `apps/core/static/core/js/workspace.js` update the accent and calculate contrast.
The sidebar uses `--tenant-background` and `--tenant-background-contrast` supplied
by `templates/base.html`; do not substitute the action color for its background.

### Neutral

The frontmatter's light roles map to `--ui-bg`, `--ui-surface`,
`--ui-surface-muted`, `--ui-text`, `--ui-muted` and `--ui-border` in
[workspace.css](apps/core/static/core/css/workspace.css). Surfaces also have
`--ui-surface-text` and `--ui-surface-muted-text`: use these within cards and portals,
rather than assuming canvas text works on every surface. The light surface contains
alpha; preserve its compositing behavior.

The workspace dark entries are CSS fallbacks, not a second selectable palette.
`html[data-theme="dark"]` resolves `--ui-dark-*` properties generated by
`workspace.js`. A pale selected surface may therefore need dark text even in dark
mode. Canvas and selected surface are independently controlled. Keep those live
variables in new UI; do not hardcode the frontmatter's fallback swatches.

### Public live palette

The marketing page restores the live INPROFIC presentation: navy hero and plan
areas with the original grid and ambient background elements, a cream canvas,
white cards with their original shadows, an overlapping bridge and the green
WhatsApp contact bubble. This authored public palette stays stable when workspace
theme preferences change. Local shared surface/text roles keep menus, dialogs,
tables and form controls readable. The workspace itself retains its theme choices.

### Semantic states

Danger actions use the existing danger fill in
[interactions.css](apps/core/static/core/css/interactions.css). Inventory low and
warning rows have separate theme-specific backgrounds, foregrounds, muted text and
hover colors in `workspace.css`'s Inventory block. Those are semantic treatments,
not tenant accents; reuse the block instead of building a parallel alert palette.

**The Neutral Text Rule.** General text and links use the local surface or canvas
foreground; tenant accents belong to fills, focus and selection.

**The Inventory Row Rule.** Ordinary table rows remain neutral. Inventory low and
warning rows keep their semantic background and matching text through hover and
sticky cells.

## Typography

**Body and Subheading Font:** Sora, with system sans fallbacks, across the public
site and workspace.
**Main Heading Font:** Fraunces, with Georgia and serif fallbacks.
**Data Font:** IBM Plex Mono for figures, including dashboard KPI values, numeric
table values and stock ticker balances; tables also use tabular numerals.

### Hierarchy

The frontmatter records the restored hero display from `marketing-restored.css`,
the workflow section headline and row-title roles from `marketing.css` with restored
heading tracking, and workspace body/action roles from `workspace.css`. Other
restored section headings retain their template size utilities, including 1.875rem
to 3rem for the module-map heading. Hero introductory copy uses 1rem to 1.125rem
and a 1.75rem line-height; the separate marketing-intro pattern remains 1.08rem,
line-height 1.8 and a 31rem measure. Public small supporting text uses .78rem. Workspace table headers use
.68rem, weight 700, .035em tracking and uppercase; this is a data header treatment.
Workspace display headings retain -.025em tracking with template-defined sizes.

Existing subsection headings (`h3`/`h4`) in the workspace use Sora at weight 750
and -.018em tracking; the plan matrix header has its own Sora treatment. Body copy, controls and subheadings use Sora. Main page headings and explicit
display headings use Fraunces; monetary amounts, counts and KPI values use IBM
Plex Mono. Snapshot images preserve the approved paper-layout Inter type.

**The Surface Type Rule.** Use Sora for body/control text and subheadings, Fraunces for main
headings, and IBM Plex Mono for figures on both public and workspace surfaces.

## Layout

The workspace content is centered with a 96rem maximum width. At 768px and above,
the fixed sidebar is 14rem wide and the main area offsets by the same amount;
main padding is 1.5rem. Narrow screens use the mobile navigation shell and 1rem
main padding. These rules come from `templates/base.html` and `workspace.css`.
Existing utility layouts supply density; the extracted spacing values are reused
steps, not a newly imposed universal scale.

The restored public shell uses a shared centered content edge and responsive gutters. The hero has a 3rem gap and
stacks below 1024px; from that breakpoint its columns have a 1.05/.95 ratio,
with 5rem top and 7rem bottom padding. Below it, top/bottom padding is 3rem/5rem.
All public sections share a 1320px content edge, with gutters clamped from 1.25rem
to 3rem and section spacing from 3.5rem to 6.5rem. Section paint spans the viewport;
semantic section classes keep gradients stable when Founder campaigns or the trust
strip are configured. Dark cards have borders and intentional solid navy fills,
without stacked shadows or backgrounds on every nested wrapper.

The module map uses one column, two from 640px and three from 1024px, with the
central logo and SVG connectors relating all nine modules. Plans and the original
FAQ use three/two columns respectively from 1024px and stack below it. Workflow
rows retain their own 900px breakpoint. Native demo windows use a 132px sidebar
and flexible content at 440px height; at 640px and below they use an 88px sidebar,
500px height and stacked KPIs. Preserve visible copy, scroll access and controls
within these illustrative frames.

Contained form dialogs use viewport clearance of 3rem, maximum width 76rem and
maximum height 58rem. Supplier, customer and finance-account forms use the compact
42rem maximum width and 48rem maximum height. At 640px and below, dialogs fill the
viewport: inset 0, width/max-width 100vw, height 100dvh, no transform, margin or
corner radius. The frame scrolls inside the dialog. Confirmations remain compact
(maximum 28rem with viewport clearance). See `interactions.css` and
[workspace.tsx](assets/ui/workspace.tsx).

Dialog titles use the shared heading-text extractor, which excludes help tips,
hidden descriptions, icons and controls. Keep the record name in the heading;
tooltip copy must never become part of the dialog title. Create-action accessible
labels should match the loaded page heading when the title is known in advance.

## Elevation & Depth

Borders and tonal surfaces organize routine work; two shared shadow tokens supply
resting and raised depth. `--ui-shadow` is the low card shadow
(`0 1px 3px rgb(15 23 42 / .10)`), while `--ui-shadow-raised` is used by panels and
dialogs (`0 8px 24px rgb(15 23 42 / .14)`). Dark mode changes both values in
`workspace.css`; the sidecar records the variants. Reuse the tokens rather than
copying a bespoke shadow onto every card. Add `workspace-card-raised` alongside
`workspace-card` when a compact summary value needs the shared raised emphasis.

[button-surfaces.css](apps/core/static/core/css/button-surfaces.css) applies a
shallow vertical highlight, inset edges and a small shadow only to marked visual
buttons. It leaves the label clear and changes the edge on hover. This requested
button finish is not a general glass material for the application. Forced colors
and print remove the finish.

The restored public demo panels and module cards remove individual shadows and
backdrop blur, separating records through borders and tone. Public dark sectional
gradients supply atmosphere behind the content; they do not alter card text roles.

**The Button Finish Rule.** Apply shallow glass only through the existing visual
button contract; links, clickable cards, status panels, tabs, switches and input
widgets do not gain it merely because they can be clicked.

## Shapes

The shared corner hierarchy is control, ordinary card, then large card, as recorded
in frontmatter and `workspace.css`. Controls have a 1px theme-aware border; ordinary
cards and portalled interaction surfaces use the card radius. Chips use pill
shapes. Mobile form dialogs deliberately become square, edge-to-edge surfaces.
Avoid adding an unrelated radius scale to Tailwind utilities or component defaults.

## Components

### Buttons

Compact and explicit. Workspace actions use the frontmatter padding and a minimum
height of 2.25rem, 14px labels and the same spacing as marked legacy text buttons.
Explicit action background and text colors take precedence over the neutral
workspace default. Icon buttons, tabs and switches keep their own sizing.
The neutral variant mixes 5% accent into the current surface,
8% on hover. Primary actions use live accent/contrast tokens. The public `ui-button` pattern uses .85rem text, weight 600, line-height 1.4 and
its recorded padding; restored hero actions retain template utility spacing and
weight. Navigation actions resolve to the shared control radius. The sidecar
contains both surface variants and the public primary example. Hover changes the
highlight edge without washing out the label; the current action also has an inset
accent underline. Preserve destructive meaning and disabled semantics.

### Cards / Containers

Neutral containers use the shared surface/text/border tokens, card radius and low
shadow. Padding belongs to the pattern: the public example ledger uses 1.5rem,
reducing to 1rem on mobile. Do not infer a universal padding token for all cards.
The explicitly labeled illustrative ledger is an existing public signature
component, not evidence of real customer data.

### Inputs / Fields

Fields use surface text, border and control radius. General minimum height is
2.25rem; form-experience fields and searchable-select triggers use 2.6rem. Focus
uses an accent border and a 3px accent-tinted ring; invalid fields keep their
visible error border and messages. The searchable selector preserves the native
select's values, required/disabled state and change events; it is not a new save
boundary. See `templates/base.html`, `workspace.css`, `interactions.css` and
`assets/ui/workspace.tsx`.

### Navigation

The tenant sidebar uses its own contrast pair and derived soft/muted tokens.
Active rows use a soft fill plus an inset accent marker, with neutral readable
labels. The mobile panel exposes expanded state and closes to an inert region.
Use the existing navigation structure rather than glass-coated navigation rows.

### Chips

Existing tenant subscription chips use the sidebar contrast color and soft fill,
with compact .625rem semibold text. Stock chips retain their own foreground inside
semantic Inventory rows. Preserve the state label; do not use color alone.

### Public module map and walkthroughs

The logo-connected module graph retains nine readable cards. Hover lifts a card
by 2px and changes its border over 160ms; activation opens a Radix detail dialog
with a module-specific preview. Keep the existing logo assets, neutral card text
and accent connector strokes. The graph is explanatory content, not an unrelated
floating decoration pattern.

Walkthroughs label their data as illustrative and provide explicit play/pause,
step tabs and module controls. The service selector exposes Bakery, Restaurant &
Food Service, General Production, Wholesale and Retail profiles. Nested panels
represent the simulated operational UI; they are not a general instruction to
nest decorative cards. The typing hero retains a complete screen-reader heading,
fixed-width word slot and monochrome cursor. Global motion pause and reduced
motion remain authoritative. The 29 original FAQ disclosures and four additional answers use
native details/summary, and workflow comparisons remain anonymous. These retained
content surfaces are not new component primitives.

### Dialogs and selection

Owned shadcn/Radix components provide dialog, confirmation, popover, menu and
searchable selection behavior. Portals use the same live surface tokens as the
workspace. Routine contained edits retain Django's form, formset prefixes,
management forms, validation and save behavior. Full-page actions remain available.
Approval, release, completion, reversal and cut-over retain deliberate review
screens. Preserve Escape handling, focus return, dirty-form recovery and visible
validation errors; do not close a failed or unauthorized save as success.

Shared controls have a 140ms transition duration; focus-visible uses a 2px
accent-mixed outline with a 2px offset. Reduced-motion overrides in the shared CSS
remove nonessential animation and transitions. Use inline SVG for new icons, with
accessible labels on controls. These are interaction requirements, not permission
to reproduce legacy glyph icons.

## Do's and Don'ts

### Do:
- Do use live shared CSS tokens for tenant/theme surfaces, local foregrounds and action contrast.
- Do preserve Inventory semantic rows, including hover and opaque sticky-cell compositing.
- Do keep workspace records compact, quantities aligned and validation errors visible.
- Do retain the live site’s Fraunces display headings and Sora body copy; use Sora for workspace body and controls, Fraunces for main headings and IBM Plex Mono for figures.
- Do preserve keyboard focus, Escape, focus return and reduced-motion behavior.
- Do retain public navy sectional gradients in dark mode and shared neutral surfaces in light mode.
- Do label simulated records as illustrative; autoplay in-view walkthroughs without playback controls, pausing for interaction and reduced motion.
- Do adapt marked brand artwork to its actual composited background; leave uploaded customer logos untouched.

### Don't:
- Don't hardcode the documented fallback palette over runtime tenant or theme choices.
- Don't apply the shallow button finish to ordinary links, cards, tabs, badges or input widgets.
- Don't introduce eyebrow labels, artificial metrics, or floating orbs. The public headline's flowing brand gradient and navy sectional gradients are explicit user requests; gradient text is not a general workspace pattern.
- Don't turn registered but unused fonts or legacy inline styling into new system roles.
- Don't replace deliberate approval, release, completion, reversal or cut-over review screens with routine edit dialogs.


### Public motion, content and controls

The original live CSS is retained in marketing-demos.css. marketing-restored.css
contains small compatibility, contrast and interaction fixes for shared assets.
Hero and module cards keep their floating motion, pausing on hover/focus; section
scroll entrances and the staggered module list remain. Reduced motion stops loops.
The typing gradient and SVG share a stable flex layout with no icon background;
a sentinel keeps the baseline stable through the empty-word frame.

Both illustrative demos autoplay while visible and pause during interaction.
All 33 FAQ answers remain. Founder campaigns, trust strip, plans, payroll tiers
and trial policy remain configured from the server. Shared Run and stock-to-sale
examples plus the anonymous workflow comparison live inside See the connected
controls, beside the storefront walkthrough. The public feature matrix lists
Shared Runs and purchased stock to sale, with no separate comparison beneath it.
The main public shell has a modest 1320px maximum width; original narrower content
containers and bridge proportions are retained.

### Snapshot exports and switches

Snapshot PNGs use the supplied procurement reference as their export context:
cream `#fffdf8` paper, dark `#262624` text, muted `#78716c`, maroon `#8f172d`
highlights, fine `#e7dfcc` rules, a white total card and an authenticated-link QR
footer. Keep these export colors stable in light/dark workspace themes. Sharing
controls and native menus retain the active workspace palette.

Shared `.cf-switch` controls retain the payroll package switch's neutral gray
track in both states. Off has a white thumb with an X; on moves the thumb right
and fills only that thumb with the active accent, using its contrast color for
Tabler double checks. Native input names, labels, events, disabled state and
keyboard operation remain authoritative. Both workspace and standalone skins
share this styling.

### Filled-surface label preference

Tenant background, accent and button fills use light labels on medium and deep colors.
Dark labels apply only to very pale fills (relative luminance at least 0.60). Client-side
workspace tokens, server-rendered Business colors and brand asset selection share this
boundary. Neutral paper snapshot colors remain stable. This product preference is not
itself a guarantee of a WCAG contrast ratio for every tenant-selected color.

Distribution paper snapshots use a compact product/customer quantity table followed
by six financial cards. They keep cream paper, Inter and maroon ink accents, bounded
rows and a QR footer. Detailed accompanying text carries overflow. Shared workspace
content reserves bottom scrolling space for actions to clear the collapsed alert tray;
embedded form dialogs do not receive that extra space.


### Operational table scrolling

`form-experience.js` automatically enhances workspace tables with a keyboard
focusable scroll region. Use standard `thead`/`tbody` markup in an overflow wrapper.
The first column remains sticky on both desktop and mobile. All mobile tables,
including read-only reports, share the form width limit of
`clamp(6.1rem, 38vw, 10.8rem)`. Child controls/text wrappers must fit that width;
full-width `colspan` date/group/footer cells do not pin horizontally. Headers
remain vertically sticky. Reuse these shared assets rather than per-page widths.
