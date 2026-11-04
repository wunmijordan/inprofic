# Staff assistant (read-only pilot)

Implements release 1 of `TENANT_ASSISTANT_PROPOSAL.md`: an Assistant tab in the
shared activity tray that answers questions about the signed-in member's business
from existing records. Desktop and mobile Assistant menu buttons open the tab
directly; `/assistant/` also opens it. It is **read-only**: it cannot create, edit, approve, post or send
anything. It is separate from the customer-facing WhatsApp assistant (different
identity, permissions, key, storage and retention).

## Who can use it

Every layer must allow it, checked on every request (page, ask, resume):

1. `ASSISTANT_ENABLED` environment switch (default on).
2. **Founder Console → Staff assistant → main switch** (default on). Off hides and
   blocks the assistant for every business immediately.
3. Access mode: the business is a **pilot** (added by slug in the Founder Console), or
   **Open to every business** is on.
4. The business's own **Assistant settings** (Business Settings → Assistant) allow it.
5. The member is a business admin of that business.

Revoking membership, demoting the role, or any switch above blocks the next request,
including resuming an old conversation. An admin who turned the assistant off for their
business can still open Assistant settings to turn it back on.

## Quick facts (work without an AI model)

A quick fact is a named question run directly against the records through a read tool,
so it is exactly as accurate as the report behind it. 22 are built in:

| Group | Quick facts |
|---|---|
| Overview | Yesterday's brief |
| Sales | Sales today / yesterday / this week / last week / this month / last month; Best sellers this week / this month |
| Money | Profit this month / last month / this week / last week; Expenses this month / this week; Cash on hand; Who owes me?; Overdue balances; What do I owe?; VAT position |
| Stock & production | What is low on stock?; Recipe vs actual; Production orders waiting |
| Approvals | Waiting for my approval |

Tap a button, or type in your own words: an explicit keyword matcher (no model, no
guessing) maps the question to a fact ("who owes me money?", "profit last month",
"what do I need to restock"). If nothing matches, or two different reports tie, the
answer says so and offers clickable suggestions. The same matcher is the fallback when
the AI model is unreachable, labelled as such. A quick fact needs the member to be able
to view the module behind it (e.g. Finance for profit).

## Controls

| Where | Who | Controls |
|---|---|---|
| Founder Console → Staff assistant | Superusers | Main switch; Quick facts on/off; AI answers on/off; Open to every business; each quick fact on/off platform-wide; pilot businesses (add by slug / remove) |
| Business Settings → Assistant settings (`/assistant/settings/`) | Business admins | Assistant on/off for this business; AI answers on/off; each quick fact on/off |

A business can only narrow what the platform allows: a fact the platform switched off
shows as unavailable and cannot be re-enabled by the business (the business's earlier
choice is kept if the platform re-enables it). Settings are cached for at most 30
seconds and invalidated on save. Business changes are written to the audit log.
Assistant settings are configuration and are not part of tenant backups.

## What the model can additionally use

For typed questions with an AI provider, the model may call the same read tools (16 in
total, including `get_order_status`, `get_run_variance`, `get_weekly_summary` and
`search_help`) and compose an answer. Every answer shows its date or range, ledger
scope, a basis note (revenue is paid sales; receivables are balances, not cash; stock
health is not a forecast; VAT is an estimate) and source links.

## Boundaries (enforced in code, not by the model)

- Business and user come from the authenticated request. No tool accepts a business or
  user id; record ids are re-queried inside the member's business, and a foreign id
  returns the same error as a missing one.
- A tool is offered only if the member can view its module, and the same check runs at
  execution. A model asking for a tool the member lacks gets an error, never data.
- Arguments are validated against a strict per-tool schema; unknown keys are rejected.
- The model gets no SQL, Python, URLs or ORM. Results are bounded (rows, size).
- Text from business records (customer names, notes) is returned wrapped as
  `untrusted_data`; the system prompt tells the model never to follow instructions in it.
  The offered tool list never changes mid-conversation.
- Provider outage or missing configuration produces a plain "not answered, nothing
  guessed" message, never a plausible figure.
- Conversations and messages belong to one member in one business; nobody else can read
  or continue them. Stored traces hold tool name and validated arguments only, never
  results or credentials.

## Limits, privacy and retention

- Per user: `ASSISTANT_REQUESTS_PER_MINUTE_PER_USER` (default 10).
- Per business per day: `ASSISTANT_DAILY_MODEL_CALLS_PER_BUSINESS` (default 300 model
  round trips). Usage is shown on the page. Quick facts do not use model calls.
- Retention: `purge_assistant_history` (in the scheduled job registry) deletes
  conversations idle longer than `ASSISTANT_RETENTION_DAYS` (default 90).
- Privacy: tool results sent to the provider can include customer names and amounts
  (for example in receivables). Use a provider and terms you are comfortable with for
  that data; secrets are never placed in tool results or traces. Conversations are
  deleted with the business or the member.
- Model usage is a variable cost: watch the usage counter before broad rollout.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ASSISTANT_ENABLED` | `1` | Platform kill switch |
| `ASSISTANT_AI_API_KEY` | empty | Provider key; empty = quick facts only |
| `ASSISTANT_AI_MODEL` | `gpt-4.1-mini` | Must support tool/function calling |
| `ASSISTANT_AI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible `/chat/completions` |
| `ASSISTANT_AI_TIMEOUT` | `25` | Seconds per model call |

The provider sits behind `assistant/providers.py`; replace it to change vendors.

## Not yet built (later releases per the proposal)

Role-aware rollout beyond business admins, draft actions with confirmation, background
jobs, uploaded-file retrieval, voice, and stock-depletion forecasts. Stock release,
receiving, production completion, financial posting, e-invoice submission, reversal and
cut-over keep their existing review boundaries.

## Rollout checklist

1. `python manage.py migrate` (adds `assistant/0002` settings tables).
2. Optionally set `ASSISTANT_AI_API_KEY`. Without it, typed questions are matched to
   quick facts only.
3. Founder Console → Staff assistant: add the pilot business by slug (or switch on
   *Open to every business*), review which quick facts to offer, save.
4. Business admins can then trim facts or turn it off in Assistant settings.
5. Verify answers against the Daily brief and Finance pages before widening.

Quick facts are deterministic and tested against the reports. Model-backed answers were
tested with a scripted fake provider, not a live model; try a real one in staging first.

## Custom questions and tray integration

Business admins can configure up to 30 distinct questions of up to 160 characters
at `/assistant/settings/`. Each selects one built-in report, including its fixed
period. A configured question is offered under **Your questions** and also matches
that exact wording (case and punctuation do not matter) when typed without a
provider. Matching does not infer SQL, invent an answer, or interpret arbitrary
instructions. Unmatched wording continues through the ordinary keyword matcher.

A mapping inherits the selected report's module permissions and its platform and
tenant quick-fact switches. Disabling the underlying report disables its mapped
questions too. Mapping configuration is tenant-owned; answers use the current
records and ledger scope at execution time. Apply migration
`assistant.0003_assistantbusinesssettings_custom_questions` before using this UI.

The tray lazily loads `/assistant/?panel=1`, keeps each conversation private to its
member/business, follows the active parent theme, and retains source links and
conversation history. Source links open the workspace outside the tray. Settings
links are shown only to users who may configure the assistant. The tray remains
available to eligible admins even when there are no outstanding alerts.
