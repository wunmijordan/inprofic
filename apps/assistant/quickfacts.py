"""Quick facts: named, deterministic questions answered straight from the records.

Each fact is a (tool, preset arguments) pair, so a quick fact is always exactly as
accurate as the report behind it and never needs an AI model. Typed questions are
matched to a fact by explicit keyword sets, which keeps "ask in your own words" useful
when no model is configured (or the model is down) without guessing.

A keyword set is a tuple of tokens that must ALL appear in the question; a token may
list alternatives with ``|`` (any one satisfies it). Longer sets are more specific and
win over shorter ones; ``exclude`` tokens veto a fact.
"""
import re
import hashlib
from dataclasses import dataclass

from .tools import available_tools

_SALE = "sale|sales|sold|sell|selling|revenue|income|takings|turnover"
_MONEY_IN = "profit|profits|profitable|pnl|p&l|earn|earned|earnings|margin|loss"
_COST = "expense|expenses|spend|spent|spending|cost|costs|overheads"
_ITEM = "stock|inventory|ingredient|ingredients|material|materials|supplies|item|items|product|products"
_LAST = "last|previous"


@dataclass(frozen=True)
class QuickFact:
    key: str
    label: str
    group: str
    tool: str
    args: tuple = ()
    keywords: tuple = ()
    exclude: tuple = ()

    @property
    def arguments(self):
        return dict(self.args)


GROUPS = ("Overview", "Sales", "Money", "Stock & production", "Approvals")

QUICK_FACTS = (
    QuickFact("brief_yesterday", "Yesterday's brief", "Overview", "get_daily_brief", (),
              (("brief|briefing|overview|recap|summary",), ("how", "business|things|we|am", "doing|going"), ("morning", "brief|report|update"))),
    QuickFact("sales_today", "Sales today", "Sales", "get_sales_summary", (("period", "today"),),
              ((_SALE, "today"), ("today", "much", "make|made|earn|earned"))),
    QuickFact("sales_yesterday", "Sales yesterday", "Sales", "get_sales_summary", (("period", "yesterday"),),
              ((_SALE, "yesterday"), ("yesterday", "much", "make|made|earn|earned"))),
    QuickFact("sales_this_week", "Sales this week", "Sales", "get_sales_summary", (("period", "this_week"),),
              ((_SALE, "week|weekly"),), exclude=(_LAST,)),
    QuickFact("sales_last_week", "Sales last week", "Sales", "get_sales_summary", (("period", "last_week"),),
              ((_SALE, _LAST, "week"),)),
    QuickFact("sales_this_month", "Sales this month", "Sales", "get_sales_summary", (("period", "this_month"),),
              ((_SALE, "month|monthly"),), exclude=(_LAST,)),
    QuickFact("sales_last_month", "Sales last month", "Sales", "get_sales_summary", (("period", "last_month"),),
              ((_SALE, _LAST, "month"),)),
    QuickFact("top_products_week", "Best sellers this week", "Sales", "get_top_products", (("period", "this_week"),),
              (("best|top|popular|bestselling|biggest|leading", "product|products|seller|sellers|selling|item|items", "week"),)),
    QuickFact("top_products_month", "Best sellers this month", "Sales", "get_top_products", (("period", "this_month"),),
              (("best|top|popular|bestselling|biggest|leading", "product|products|seller|sellers|selling|item|items"),)),
    QuickFact("profit_this_month", "Profit this month", "Money", "get_profit_summary", (("period", "this_month"),),
              ((_MONEY_IN, "month|monthly"), (_MONEY_IN,)), exclude=(_LAST, "owe|owes|owed")),
    QuickFact("profit_last_month", "Profit last month", "Money", "get_profit_summary", (("period", "last_month"),),
              ((_MONEY_IN, _LAST, "month"),)),
    QuickFact("profit_this_week", "Profit this week", "Money", "get_profit_summary", (("period", "this_week"),),
              ((_MONEY_IN, "week|weekly"),), exclude=(_LAST,)),
    QuickFact("profit_last_week", "Profit last week", "Money", "get_weekly_summary", (),
              ((_MONEY_IN, _LAST, "week"), ("weekly", "summary|report|profit"))),
    QuickFact("expenses_this_month", "Expenses this month", "Money", "get_expenses_summary", (("period", "this_month"),),
              ((_COST, "month|monthly"), (_COST,)), exclude=(_LAST, "week|weekly", "owe|owes|owed|pay|payable|payables")),
    QuickFact("expenses_this_week", "Expenses this week", "Money", "get_expenses_summary", (("period", "this_week"),),
              ((_COST, "week|weekly"),), exclude=(_LAST,)),
    QuickFact("cash_position", "Cash on hand", "Money", "get_cash_position", (),
              (("cash|bank|balance|balances|accounts", "hand|position|have|left|much|total|now"), ("bank|cash|account|accounts", "balance|balances"), ("cash|bank",))),
    QuickFact("owed_to_me", "Who owes me?", "Money", "get_receivables", (),
              (("owe|owes|owed|debtor|debtors|receivable|receivables|outstanding|unpaid", "me|us|money|customer|customers|debt|debts"),
               ("debtor|debtors|receivable|receivables",))),
    QuickFact("overdue", "Overdue balances", "Money", "get_overdue_receivables", (),
              (("overdue|late|ageing|aging|oldest", "debt|debts|balance|balances|payment|payments|receivable|receivables|invoice|invoices|customer|customers|debtor|debtors"),
               ("overdue",))),
    QuickFact("i_owe", "What do I owe?", "Money", "get_payables", (),
              (("owe|owing|payable|payables|creditor|creditors", "i|we|supplier|suppliers|vendor|vendors"), ("payable|payables|creditor|creditors",)),
              exclude=("vat|tax|taxes",)),
    QuickFact("vat_position", "VAT position", "Money", "get_vat_position", (("period", "this_month"),),
              (("vat|tax|taxes", "owe|due|pay|position|return|much|month|payable"), ("vat",))),
    QuickFact("low_stock", "What is low on stock?", "Stock & production", "get_stock_health", (),
              (("low|running|short|out|reorder|restock|shortage|finished", _ITEM), ("stock|inventory", "low|left|short|level|levels|health"),
               ("running", "low|short|out"), ("restock|reorder|reordering|restocking|restocked",))),
    QuickFact("recipe_check", "Recipe vs actual", "Stock & production", "get_run_variance", (),
              (("recipe|variance|wastage|waste|shrinkage|shrink", "actual|variance|check|waste|usage|ingredient|ingredients|use|used"), ("variance",))),
    QuickFact("orders_pending", "Production orders waiting", "Stock & production", "get_order_status", (("status", "pending"),),
              (("production|kitchen|bake|baking|batch|batches", "order|orders"),)),
    QuickFact("approvals", "Waiting for my approval", "Approvals", "get_pending_approvals", (),
              (("approval|approvals|approve|awaiting|pending|waiting", "request|requests|purchase|purchases|po|approval|approve|me"), ("approval|approvals|approve",))),
)
FACTS = {f.key: f for f in QUICK_FACTS}
_TOKEN = re.compile(r"[a-z0-9&]+")


def _tokens(text):
    return set(_TOKEN.findall((text or "").lower().replace("what's", "what is")))


def _hit(spec, tokens):
    return bool(tokens & set(spec.split("|")))


def _score(fact, tokens):
    if any(_hit(spec, tokens) for spec in fact.exclude):
        return 0
    best = 0
    for group in fact.keywords:
        if group and all(_hit(spec, tokens) for spec in group):
            best = max(best, len(group))
    return best


def normalize_question(question):
    return " ".join(_TOKEN.findall((question or "").lower()))


def match(question, facts):
    """Best quick fact for a typed question among ``facts``.

    Returns ``(fact, suggestions)``. ``fact`` is None when nothing matches or two facts
    tie on different reports (ambiguous); ``suggestions`` then lists a few to offer.
    """
    normalized = normalize_question(question)
    exact = [f for f in facts if normalize_question(f.label) == normalized]
    if exact:
        return exact[0], []
    tokens = _tokens(question)
    scored = sorted(((_score(f, tokens), -i, f) for i, f in enumerate(facts)), key=lambda row: (row[0], row[1]), reverse=True)
    scored = [row for row in scored if row[0] > 0]
    if scored and (len(scored) == 1 or scored[0][0] > scored[1][0] or scored[0][2].tool == scored[1][2].tool):
        return scored[0][2], []
    if scored:   # genuine tie between different reports: offer the tied facts
        top = scored[0][0]
        return None, [row[2] for row in scored if row[0] == top][:4]
    defaults = [f for key in ("brief_yesterday", "sales_today", "owed_to_me", "low_stock") for f in facts if f.key == key]
    return None, defaults


def enabled_facts(user, business):
    """Quick facts this member can run in this business right now (every layer must allow it)."""
    from . import config
    permitted = {t.name for t in available_tools(user, business)}
    facts = [f for f in QUICK_FACTS if f.tool in permitted and config.fact_allowed(business, f.key)]
    from .models import AssistantBusinessSettings
    row = AssistantBusinessSettings.raw_objects.filter(business=business).first()
    allowed = {f.key: f for f in facts}
    for item in (row.custom_questions if row else []) or []:
        target = allowed.get(item.get("report"))
        label = str(item.get("question", "")).strip()
        if target and label:
            key = "custom_" + hashlib.sha256(label.casefold().encode()).hexdigest()[:24]
            facts.append(QuickFact(key, label, "Your questions", target.tool, target.args))
    return facts


def grouped(facts):
    return [{"name": g, "facts": [{"key": f.key, "label": f.label} for f in facts if f.group == g]} for g in (*GROUPS, "Your questions")
            if any(f.group == g for f in facts)]
