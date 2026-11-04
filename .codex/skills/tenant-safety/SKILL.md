---
name: tenant-safety
description: >
  Audit StoreTrack changes for multi-business tenant isolation. Use for new
  models, query changes, admin/user management, reports, exports, finance,
  Shared Production Runs, or any feature that reads IDs from a request.
version: "1.0"
updated: 2026-10-28
---

# Tenant Safety (StoreTrack)

## Invariants

- `request.business` is the request tenant.
- Top-level tenant data normally inherits `BusinessOwnedModel`.
- Request views prefer scoped `.objects`. Books, tax, cross-period settlement and
  cut-over administration may deliberately bypass period filters with
  `.raw_objects`; those queries must explicitly filter `business=request.business`
  (or a verified tenant-owned parent). A period bypass never bypasses tenancy.
- New BusinessOwnedModel rows are assigned `request.business` server-side.
- A submitted object ID is not trusted until re-queried through a tenant-scoped
  queryset.
- Cross-business superuser operations must be explicit and permission checked.
- Business-local Order numbering never substitutes for the globally unique DB
  primary key in URLs/FKs.

## Review procedure

For every changed queryset, ask:

1. What tenant does this query execute under?
2. Can a user alter a POST/GET ID to fetch another business's object?
3. Is a related child implicitly scoped through a verified parent?
4. Does an export/report use the same business scope as the screen?
5. Does superuser behavior intentionally differ from Business Admin behavior?

Flag accidental global access as a correctness/security bug, not a UI issue.
