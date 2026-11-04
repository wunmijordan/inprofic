# Workspace UI and browser checks

The Django templates and shared workspace/form-experience styles remain the
presentation contract. Tailwind provides layout utilities; the workspace uses
compact Tabler-style spacing and hierarchy. Inter is the operational typeface.
Shared radius and elevation tokens control cards and controls across themes.

## Interactive components

The owned shadcn/ui sources in `assets/ui/components` use Radix primitives.
`assets/ui/workspace.tsx` mounts the form dialog, confirmation dialog, searchable
native-select enhancements, appearance popover and theme dropdown. Source and
license provenance are in `assets/ui/THIRD_PARTY.md`.

Routine add/edit links for inventory, suppliers, purchases, customers, sales,
expenses, cash accounts, payments, stock adjustments, orders and Shared Runs
open the existing Django form in a same-origin dialog. The original field names,
CSRF, permission checks, formset prefixes, page scripts and server validation
remain authoritative. A failed save remains open with its errors; a successful
save reloads the parent workspace. Direct URLs and “Open full page” remain
available. Closing an edited form asks before discarding changes.

Approval, receiving, material release, completion, reversal and ledger cut-over
review stay on their existing review screens. An explicit `data-no-dialog` link
opts out of the routine-form allowlist. `data-native-dialog` preserves a native
HTML dialog where a specialized integration requires it. Other existing native
detail dialogs retain their `showModal`/`close` APIs through the Radix bridge.

Enhanced selects retain the original native field as the POST source. Values,
required/disabled states, reset, option changes, `ms-refresh` and input/change
events remain available to pricing and formset scripts. `data-native-select`
opts out. The no-JavaScript fallback is the normal full page and native field.
Same-origin framing is enabled by `X_FRAME_OPTIONS = 'SAMEORIGIN'`; external
sites cannot frame these pages.

## Buttons and logos

Glass is decorative and only applies to explicitly marked **visual buttons**:
`data-button-surface` or shared button classes. Clickable cards, navigation,
badges, status containers, tabs and plain text controls are not automatically
buttons. Primary fills follow the selected accent; secondary labels remain
neutral; warning and destructive semantics are preserved.

`brand-assets.js` chooses marked wordmarks against their actual composited
background. It responds to live palette and theme changes, including pale
surfaces in dark mode. It does not replace customer-uploaded logos. Offline and
OS launch artwork retain their dedicated theme behavior.

## Build and verify

```bash
npm ci
npm run build
npx playwright install --with-deps chromium
npm run test:ui
```

The Chromium suite launches `scripts/ui/server.py`, creates a temporary SQLite
database from the current model schema, seeds two local businesses and test
roles, and removes the database when the server stops. It does not use the
normal application database or validate migration upgrade paths. Test-only
sign-in routes exist only in `scripts.ui.urls`, never in production URLs.

Set `INPROFIC_UI_PYTHON` to a Python executable if it is not `venv/bin/python`.
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` may point to an existing Chrome/Chromium
binary. Reports, screenshots and traces are in ignored `playwright-report/` and
`test-results/`. CI runs the same suite.

For browser exploration use `npm run ui:inspect -- http://127.0.0.1:8001/` and
the official `.codex/skills/playwright-cli/SKILL.md`. The skill is copied from
the pinned `@playwright/cli` package; refresh it with `npm run skills:sync`
after deliberately updating that dependency. Skill changes are available to
subsequent agent turns; never use the fixture sign-in URLs on a live server.

## Public product information

The marketing page demonstrates a proportional Shared Run example, includes
real configured prices/promotions/trial lengths and shows only configured trust
logos/counts. Public and in-app plans share the same entitlement-derived feature
matrix and anonymous workflow comparison partial. The comparison describes
connected controls versus fragmented processes without naming other products or
asserting that every alternative lacks a feature. It does not promise automatic
tax-provider clearance.

The original typing headline, nine-module logo-connected explorer, five business
service profiles, general and storefront/POS walkthroughs, promotions, trust strip,
support link and all 29 original FAQ entries are preserved. Four newer setup
answers and the proportional Shared Run example supplement them. The original
demo geometry lives in `marketing-demos.css`; shared theme roles and responsive
refinements live in `marketing-restored.css`. The public dark theme uses brand
navy (`#050733`) with sectional gradients; light mode keeps its neutral canvas.
`marketing-demos.js` owns the static service scenarios, playback, typing and FAQ
behavior. Module previews use the shared Radix dialog bridge. Demonstrations are labeled illustrative and have explicit playback
controls; the headline, connector and trust-strip motion can be paused.

The proposed staff assistant is documented separately in
`docs/TENANT_ASSISTANT_PROPOSAL.md`; it is not advertised as an implemented feature.

## Design skills

The project includes Impeccable 4.8.0 from `pbakaus/impeccable` and Vercel’s Web
Design Guidelines from `vercel-labs/agent-skills`, installed through the Codex
skill installer. The frontend skill routes substantial work through their visual
and accessibility guidance while preserving INPROFIC’s shared CSS and domain
constraints. `PRODUCT.md` records confirmed product facts; `DESIGN.md` records
the current design. The Impeccable launcher is executable and its project-context
check has run. No automatic agent hooks were added; use its explicit detector as
part of a finished UI review. Newly installed skills are available on subsequent
agent turns and can also be read directly during the current implementation.
