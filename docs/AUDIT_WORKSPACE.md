# Audit Workspace

Audit Workspace is a plan-gated, role-isolated evidence room for external auditors and internal reviewers.

## Why it exists

An external auditor needs broad evidence across the business, but should not receive normal operational navigation such as Inventory edit screens, Finance forms, Procurement receiving screens or Production completion actions. INPROFIC therefore exposes an auditor-specific workspace that reads tenant-scoped evidence internally while keeping the auditor's ordinary app permissions narrow.

## Entitlement and role boundary

Audit Workspace is controlled by the founder-managed `audit` plan entitlement. The seeded **External Auditor** role receives:

```text
Audit Workspace: View = Yes
Audit Workspace: Edit = No
```

The role is not automatically granted Inventory, Procurement, Production, Sales, Expenses, Finance, Reports, Users or Commerce.

When an external auditor signs in without Dashboard access, the middleware sends them directly to Audit Workspace. If they guess ordinary module URLs, the central permission middleware blocks those routes.

## Evidence included

The workspace consolidates tenant-scoped read-only evidence including:

- material balances, material valuation and reorder context;
- finished goods, product categories, product source and estimated cost;
- bill-of-material / recipe records;
- procurement orders, supplier payables and purchase line values;
- sales, sales lines, receivables and customer payment state;
- expenses and cash movements;
- production batches, production cost snapshots and quality checks;
- measurement-basis change records;
- delivery assignments, provider references and delivery events;
- platform audit log entries.

The Excel export contains the same audit-oriented evidence sheets, including auditor query records.

## Auditor queries and flags

Auditors can raise a general query or press **Query** beside supported evidence rows to pre-bind the module, record model, record ID and display label without needing edit access to operational modules. A query records:

- the module or evidence area;
- the record label, model name and record ID when known;
- subject and detailed message;
- severity;
- status.

Business Admins and users with Audit Workspace edit permission can respond, assign, mark a query as reviewing/answered/closed and preserve the response in the audit export. This allows accountants, managers or admins to handle auditor questions only when they are intentionally granted the reviewer-level audit permission.

## Read-only guarantee

Creating an audit query does not mutate the underlying stock, finance, procurement, production, sales or delivery record. It creates a separate `AuditQuery` item and an audit-log entry. Operational correction remains the responsibility of the appropriate app module and authorized internal role.
