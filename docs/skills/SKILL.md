---
name: storetrack-reference
description: >
  Human-readable index for INPROFIC repository prompt skills. Coding agents
  should start with CLAUDE.md and load a focused .claude/skills/*/SKILL.md file
  when the task matches that domain.
version: "1.0"
updated: 2026-09-04
---

# INPROFIC Prompt Skills

INPROFIC includes project-local prompt skills under `.claude/skills/` and a Codex mirror under `.codex/skills/`. The root `AGENTS.md` is the Codex/VS Code entry point.
They are documentation/instruction assets for coding agents; they do not run in
Django, change application permissions, or require a model/provider/API key.

## Skills

| Skill | Use when |
| --- | --- |
| `storetrack` | Cross-app or architecture-heavy INPROFIC work |
| `frontend-design` | Django template/UI/formset/dashboard work |
| `excalidraw-diagram` | Architecture, process, and business-pitch diagrams |
| `simplify` | After every implementation |
| `tenant-safety` | Tenant-owned models, querysets, user/admin, exports, reports |
| `production-integrity` | Recipes, production, Shared Runs, stock, QC, offcuts, reversal |
| `finance-integrity` | Cash, payments, receivables/payables, Sales, reversals, Finance analytics |
| `migration-safety` | Any model/schema change or when remote migrations exist |
| `systematic-debugging` | Tracebacks, silent forms, dynamic formsets, multi-app bugs |
| `code-reviewer` | Deep review before packaging substantial/cross-domain changes |
| `release-communications` | User-visible releases; update feature catalogue, Q4 social calendar and externally consumed docs |

## Skill format

Each skill is a directory containing a `SKILL.md` with YAML-style front matter:

```markdown
---
name: production-integrity
description: Preserve production invariants...
version: "1.0"
updated: 2026-09-04
---

# Production Integrity
...
```

Skills may later grow `references/` or `scripts/` directories if a prompt needs
stable reference material or safe deterministic tooling. Avoid adding scripts
that mutate the live database implicitly.

## Runtime AI is separate

This repository-level skill library does **not** mean INPROFIC has a runtime
AI assistant. A future runtime prompt/AI feature should be implemented
separately, with explicit provider configuration, tenant-safe prompt context,
permissions, logging, failure handling and cost controls.
