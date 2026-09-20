# INPROFIC — Codex project instructions

This file is the persistent project instruction entry point for OpenAI Codex.
It applies to the repository unless a more-specific nested `AGENTS.md` overrides it.

## Start here

1. Read `README.md` for project orientation.
2. For substantial INPROFIC work, read `.codex/skills/storetrack/SKILL.md`.
3. Load the focused skill(s) below when the task matches.
4. User instructions take precedence over skill guidance. Skills are safeguards and workflows, not reasons to ignore an explicit user request.
5. Before packaging a change, apply `.codex/skills/simplify/SKILL.md` plus the relevant domain-integrity skill.

## Project skills

| Skill | Load when |
| --- | --- |
| `storetrack` | Cross-app, architectural, or broad INPROFIC changes |
| `frontend-design` | Django templates, forms, formsets, modals, dashboards, tables, navigation, UI/UX |
| `excalidraw-diagram` | Architecture diagrams, process maps, pitch visuals, diagrammatic documentation |
| `production-integrity` | Recipes, production, Shared Runs, stock, batches, offcuts, reversal |
| `finance-integrity` | Money movement, payments, Sales, receivables/payables, finance reversal |
| `tenant-safety` | Business isolation, roles, users, reports/exports, upcoming multi-tenancy |
| `migration-safety` | Models/schema/migrations, especially when remote migrations may exist |
| `systematic-debugging` | Tracebacks, silent forms, formsets, JS wiring, cross-app bugs |
| `code-reviewer` | Deep review of substantial or cross-domain changes |
| `release-communications` | User-visible feature additions/changes; keep marketing catalogue, social calendar and public integration docs synchronized |
| `simplify` | Post-implementation cleanup and invariant check |

All skill files live at `.codex/skills/<skill>/SKILL.md`.

## Critical INPROFIC invariants

- Preserve business scoping. Do not make a Business-owned query global by accident.
- Stock-mutating operations must remain auditable and transactional.
- Raw-material recipe usage is proportional to output against the product's registered batch yield.
- Shared Production Runs coordinate multiple normal orders; they do not collapse customers into one commercial order.
- Do not double-release raw materials between individual approval and Shared Run approval.
- Keep customer demand, planned production/offcut, unexpected excess, wastage, and shortage distinct.
- Reversal is compensating/auditable; deletion must never be used as an implicit stock/finance reversal.
- Inspect the actual latest migration files in the working tree before creating a new migration number.
- Subscription/module entitlements gate user surfaces and authorization only; never use a disabled entitlement to suppress interconnected stock, sales, finance, payment, audit, or other domain records that the active workflow must keep synchronized.

## Validation

Run the strongest checks available in the environment. At minimum for Python changes, compile/import-check changed modules. For template/formset changes, inspect prefixes, management forms, JS selectors, dynamic row defaults, and visible validation errors. If full Django checks cannot run, state that explicitly rather than claiming they passed.
