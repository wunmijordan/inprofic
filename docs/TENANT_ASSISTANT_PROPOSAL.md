# Tenant-scoped operational assistant — proposal

Status: release 1 (read-only pilot for selected business admins) is implemented; see
`STAFF_ASSISTANT.md`. Later releases below remain proposals. Not a marketing claim.

## Existing foundations

INPROFIC already has business memberships and module permissions, tenant-scoped
operational services, a permission-filtered Daily brief, dashboard summaries,
Books/tax workspaces and an audit trail. The customer-facing WhatsApp assistant in
`apps/whatsapp_storefront/assistant.py` already combines deterministic catalogue /
checkout handling with an optional AI reply adapter. That adapter is deliberately
limited to approved customer guidance; it is not a staff analytics assistant.

A staff assistant can reuse the domain services and provider configuration patterns.
Its identity, permissions, tools and conversation storage should be separate from
public storefront conversations.

## Recommended first release

Put an assistant panel in the authenticated workspace with questions such as:

- What sold yesterday, and which customer balances are still outstanding?
- Which materials are below their reorder thresholds?
- What production orders are awaiting approval or completion?
- Explain the difference between planned output and recorded yield on this run.
- How do I record this transaction correctly in INPROFIC?

Answers should identify their time range and Live/Backlog/Combined scope, link to
source records and distinguish recorded facts from estimates. Stock depletion
forecasts need an explicit method, sample window and insufficient-data state.
Cash received, revenue and outstanding balances are different measures.

Start with read tools and navigation. Keep the assistant visibly unavailable or
show a useful retry path when the model provider is unavailable; do not invent a
plausible operational answer.

## Request and tool boundary

```text
Authenticated workspace panel
  -> Django endpoint binds user, active business and ledger context
  -> Permission-filtered tool registry + model adapter
  -> Small validated tool calls into existing domain services
  -> Scoped results with source URLs, timestamps and calculation metadata
  -> Answer + source cards + proposed next action
```

The endpoint derives business identity from the authenticated request. Never trust
a business ID supplied in a chat message or model tool arguments. Re-query every
record ID within that business; check module permissions at tool execution as well
as when offering the tool. Re-check membership when resuming a conversation or
executing a queued job. Explicitly initialize/reset business and ledger context
in background jobs; do not rely on thread-local context inherited from a request.

Suggested bounded read tools: `get_daily_brief`, `get_stock_health`,
`get_receivables`, `get_payables`, `get_order_status`, `get_run_variance` and
`search_help`. Bind tenant/user context server-side, accept validated filters,
return bounded/paginated results and use existing calculation helpers. Preserve
intentional cross-period Books/tax semantics through explicitly business-scoped
reads. Do not let the model run SQL, Python, arbitrary URLs or unrestricted ORM.

A strict JSON tool schema constrains arguments; it does not replace authorization.
Use a provider adapter so the product need not depend on one model vendor. If using
OpenAI, use its current supported function-calling API with strict schemas; avoid
building on a deprecated visual agent builder.

## Knowledge, conversation and privacy

Retrieve product help from reviewed documentation. Private documents and uploaded
files require business ownership and their own visibility rules at retrieval time.
A tenant namespace alone does not grant every employee access to payroll or finance.
Database tools should supply current amounts; embeddings should not become a stale
copy of the ledger. Treat stored customer/supplier text and retrieved documents as
untrusted data, not instructions or permission changes.

Store conversations, tool traces, attachments, retrieval indexes and usage counters
with business/user ownership and explicit retention rules. Scope cache keys by
business, user/permission scope, ledger filters and freshness. Revalidate access
before returning a cached answer, and clear the panel on business switch. Minimize
personal data sent to a provider; redact secrets and credentials from traces.

## Controlled actions in a later release

Begin with draft purchase orders, draft customer communications and form prefill.
A preview must show exact recipients, quantities, amounts and affected records.
On explicit user confirmation, the server rechecks permissions and current state,
verifies that the confirmed payload matches the preview, and invokes the existing
transactional domain service with an idempotency key. Reused or expired confirmations
must not repeat an action. Record the human actor and assistant provenance.

Stock release, receiving, production completion, financial posting, e-invoice
submission, reversal and ledger cut-over retain their current review boundaries.
Assistant access never bypasses them. Disabled entitlements gate surfaces and tools,
not the interconnected stock/finance/audit records an authorized workflow must write.

## Rollout and acceptance

1. Read-only pilot for selected business admins; source-backed operational answers.
2. Role-aware staff rollout with help, navigation and controlled drafts.
3. Confirmed low-risk actions; optional voice after the text flow is dependable.

Release checks should include cross-tenant and cross-role ID substitution, business
switches, revoked membership, stale caches, prompt injection in stored records,
ambiguous dates/units, missing data, provider outages and duplicate action requests.
Compare computed answers with existing reports and Decimal calculations. Add per-
business quotas, rate limits, timeouts and usage visibility before broad rollout.
Model/API usage adds a variable operating cost; measure it before setting prices.

## Reference patterns

- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling): structured tool arguments; the application executes and authorizes the call.
- [OpenAI agent safety](https://developers.openai.com/api/docs/guides/agent-builder-safety): untrusted content, constrained data flow and review of consequential actions.
- [Zappie public page](https://zappie.ai/): reference for a visible business assistant and concrete interactive product demonstrations, not an implementation specification or copied claim.
