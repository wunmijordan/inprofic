# Test status after the system review

Latest run: 3 November 2026. **868 tests, 868 passed, 0 failed, 0 errored.**

| App | Tests |
|---|---|
| accounts | 130 |
| assistant | 75 |
| commerce | 212 |
| core | 248 |
| inventory | 39 |
| procurement | 36 |
| production | 109 |
| sales | 8 |
| whatsapp_storefront | 11 |

Environment: isolated SQLite test database created from repository migrations;
actual URL configuration; local-memory cache and email. Test modules are listed
explicitly (`manage.py test <modules>`) because label discovery does not work
for the namespace-style `apps/*` packages. `manage.py check` and
`makemigrations --check` are clean.

## History

The original review (29 October 2026) recorded 729 tests with 36 failures and
9 errors. Those were mostly assertions on old copy, markup, return values and
workflow rules, and were reconciled by the integrity-fix work. The state of the
delivered tree before the follow-up was 758 tests with 2 failures, both stale
assertions:

- `core.test_ledger`: expected a PO status of `ordered` after a rejected
  receipt; approved requests are now `approved`.
- `core.test_accounting`: looked for dashboard search script text in the page;
  the script is now the cached static `dashboard-search.js`.

Both were updated, and two regression tests were added
(`test_finance_receivables_and_dashboard_ignore_later_product_yield` and
`test_raising_amount_of_payment_settled_expense_reopens_the_balance`), giving 760. The Daily brief date selection and weekly summary then added 12 brief tests
and 1 procurement wording test, giving 773. The read-only staff assistant added 34 tests
and one brief permission test, giving 808. Quick-fact expansion and configuration layers, procurement WhatsApp sharing and
stock visibility then gave 868.

## Not covered by this run

SQLite cannot reproduce cross-worker PostgreSQL row-lock contention, and
provider sandboxes (Paystack, Meta WhatsApp, delivery providers) and the
production scheduler need verification in the deployment environment.
