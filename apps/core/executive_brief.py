"""Full executive report text and deliberately bounded image highlights.

Period flows and current positions are labelled separately. No stock or financial
records are changed, and quantities in different registered units never combine.
"""
from decimal import Decimal

from django.db.models import Count, ExpressionWrapper, F, Prefetch, Sum
from django.utils import timezone

from accounts.services import user_has_permission
from . import ledger
from .accounting import _sale_expressions, profit_and_loss
from .complete_summaries import MONEY, pretty
from .daily_brief import _money
from .quantities import format_quantity, format_product_quantity, material_stock_label, has_value
from .share_text import bullet

ZERO = Decimal('0')


def selected_report_sections(preferences, mode):
    """Upgrade older saved choices once; retain explicit non-sales opt-outs."""
    if not isinstance(preferences, dict):
        return None
    selected = preferences.get(mode)
    known = preferences.get('known_sections', {})
    known = known.get(mode, []) if isinstance(known, dict) else []
    if selected is not None and any(key in selected for key in ('sales','production')) and 'product_use' not in known:
        return list(dict.fromkeys([*selected, 'product_use']))
    return selected


SECTION_LABELS = {
    'sales': 'Sales and customer breakdown', 'profit': 'Profit and loss',
    'expenses': 'Expenses', 'position': 'Cash accounts and who owes / is owed',
    'cash_flow': 'Money in and money out', 'materials': 'Material stock levels',
    'products': 'Product stock levels',
    'product_use': 'Staff and gifting issues',
    'production': 'Production and approval queue', 'purchasing': 'Purchasing and open requests',
    'commerce': 'Commerce activity', 'delivery': 'Delivery activity', 'ledger': 'Ledger controls',
}
TITLE_KEYS = {
    'Sales activity': 'sales', 'Profit and loss': 'profit', 'Expenses': 'expenses',
    'Cash and outstanding balances': 'position', 'Cash movement': 'cash_flow',
    'Material stock': 'materials', 'Product stock': 'products',
    'Staff and gifting': 'product_use',
    'Production': 'production', 'Purchasing': 'purchasing',
    'Commerce': 'commerce', 'Delivery': 'delivery', 'Ledger controls': 'ledger',
}
HIGHLIGHT_KEYS = {
    'Recorded sales': 'sales', 'Paid-sales revenue': 'profit', 'Gross profit': 'profit',
    'Operating costs': 'profit', 'Cash / account balances': 'position', 'Receivables': 'position',
    'Payables': 'position', 'Material stock alerts': 'materials', 'Product stock alerts': 'products',
    'Production attention': 'production',
    'Open purchase requests': 'purchasing', 'Purchases & expenses': 'purchasing', 'Expenses': 'expenses',
    'Staff and gifting': 'product_use',
}


def _customer_payment_labels(sales, business, money):
    """Current settlement of period invoices, including later receipts/returns."""
    from sales.models import CustomerPayment, return_prefetch

    totals = {}
    invoices = sales.prefetch_related(
        'items__finished_good', return_prefetch('items__distribution_returns'),
        Prefetch('payments', queryset=CustomerPayment.raw_objects.filter(business=business)),
    )
    for sale in invoices:
        name = sale.customer or ''
        due, received = sale.net_total, sale.received_value
        values = totals.setdefault(name, [ZERO, ZERO])
        values[0] += due
        values[1] += received

    labels = {}
    for name, (due, received) in totals.items():
        balance = max(ZERO, due - received)
        if not due:
            labels[name] = 'No balance due'
        elif not balance:
            labels[name] = 'Fully paid'
        elif received:
            labels[name] = f'Partially paid · Balance: {money(balance)}'
        else:
            labels[name] = f'Unpaid · Balance: {money(balance)}'
    return labels


def enrich(brief, start, end, *, user=None, selected_sections=None):
    """Add complete, permission-scoped sections without changing legacy tool fields."""
    business = brief['business']
    allowed = {module: user is None or user_has_permission(user, business, module, 'view')
               for module in ('sales', 'finance', 'expenses', 'inventory', 'production', 'procurement', 'commerce', 'delivery', 'ledger')}
    sections, highlights, available_sections = [], [], []
    from inventory.models import FinishedGood
    product_lookup = {good.pk: good for good in FinishedGood.objects.filter(
        business=business).select_related('portion_profile')} if any(allowed[key] for key in ('sales','inventory','production','procurement')) else {}
    def product_quantity(amount, product_id, unit):
        return format_product_quantity(amount, product_lookup.get(product_id), unit=unit)
    money = lambda value: _money(business, value)

    def section(title, lines):
        lines = compact_lines(lines)
        key = TITLE_KEYS[title.split(' · ')[0]]
        available_sections.append(key)
        if lines:
            sections.append({'key': key, 'title': title, 'lines': lines})

    def highlight(label, value, detail=''):
        if has_value(value):
            highlights.append({'label': label, 'value': str(value), 'detail': detail})

    if allowed['sales']:
        from sales.models import Sale, SaleItem
        sales = Sale.objects.filter(business=business, date__range=(start, end))
        items = SaleItem.objects.filter(sale__in=sales)
        _field, line, _cogs, units = _sale_expressions(prefix='')
        total = items.aggregate(value=Sum(line))['value'] or ZERO
        count = sales.count()
        lines = [f"Sales: {money(total)} · {count} transactions"] if total else []
        if count and total:
            lines.append(f"Average sale: {money(total/count)}")
        for field, label in [('sale__source', 'Channel'), ('sale__transaction_type', 'Payment status')]:
            rows = items.values(field).annotate(value=Sum(line), count=Count('sale_id', distinct=True)).order_by(field)
            lines += [f"{label} · {Sale(business=business, source=r[field]).display_source if field == 'sale__source' else pretty(r[field])}: {r['count']} sale{'s' if r['count'] != 1 else ''} · {money(r['value'])}" for r in rows]
        customer_payments = _customer_payment_labels(sales, business, money)
        if count:
            lines.append('Payment status includes payments and returns recorded to date.')
        rows = items.values('sale__customer').annotate(value=Sum(line), count=Count('sale_id', distinct=True)).order_by('-value', 'sale__customer')
        products = items.values(
            'sale__customer', 'sale__source', 'finished_good_id', 'finished_good__name', 'finished_good__unit').annotate(
            quantity=Sum(units), value=Sum(line)).order_by('sale__customer','sale__source','finished_good__name')
        customer_products = {}
        for row in products:
            if not row['quantity'] and not row['value']:
                continue
            customer_products.setdefault(row['sale__customer'] or '', []).append(
                f"  {row['finished_good__name']}: {product_quantity(row['quantity'],row['finished_good_id'],row['finished_good__unit'])}" + (f" · {money(row['value'])}" if row['value'] else '') + f" · {Sale(business=business,source=row['sale__source']).display_source}")
        returns_by_customer = {}
        if allowed['inventory']:
            from .brief_activity import customer_return_lines
            returns_by_customer = customer_return_lines(business, start, end)
            if returns_by_customer:
                lines.append('Return destinations include later recorded activity; transfers are shown separately from sales.')
        for row in rows:
            name = row['sale__customer'] or ''
            lines.append(f"Customer · {name or 'Walk-in'}: {row['count']} sale{'s' if row['count'] != 1 else ''} · {money(row['value'])} · {customer_payments[name]}")
            lines.extend(customer_products.get(name, []))
            lines.extend(returns_by_customer.pop(name, []))
        for name, return_lines in returns_by_customer.items():
            lines.append(f'Customer · {name or "Walk-in"} · Returns from earlier sales')
            lines.extend(return_lines)
        section('Sales activity · selected period', lines)
        highlight('Recorded sales', money(total), f'{count} sales · includes unpaid / part-paid')
        brief['executive_sales_total'] = total

    if allowed['finance']:
        pl = brief.get('pl') or profit_and_loss(business, start, end)
        brief['executive_pl'] = pl
        metrics = [('Paid-sales revenue',pl['revenue']),('Cost of goods',pl['cogs']),
            ('Gross profit',pl['gross_profit']),('Operating expenses',pl['operating_expenses']),
            ('Payroll',pl['payroll']),('Operational supplies',pl['supplies']),('Stock write-offs',pl['write_offs'] - pl.get('product_use',ZERO)),
            ('Staff / gifting inventory cost',pl.get('product_use',ZERO)),
            ('Total costs',pl['total_costs']),('Net profit / loss',pl['net_profit']),
            ('Unpaid sales',pl['unpaid_sales']),('Unpaid expenses',pl['unpaid_expenses'])]
        lines = [f'{label}: {money(value)}' for label,value in metrics if value]
        lines += [f"{r['label']}: {money(r['amount'])}" for r in pl['expense_lines'] if r['amount']]
        if 'revenue_change' in brief and any(pl[key] for key in ('revenue','total_costs','net_profit')):
            from .daily_brief import trend
            against = 'same days last week' if brief['partial'] else 'last week'
            lines += [f"Revenue vs {against}: {trend(business, brief['revenue_change'], True)}",
                      f"Net profit vs {against}: {trend(business, brief['net_change'], False)}"]
        section('Profit and loss · selected period', lines)
        trend = brief.get('revenue_change')
        detail = 'Fully paid sales only'
        if trend:
            detail += f" · {trend['direction']} {money(abs(trend['amount']))} vs comparable previous week"
        highlight('Paid-sales revenue', money(pl['revenue']), detail)
        highlight('Gross profit', money(pl['gross_profit']), f"Margin {margin(pl['gross_margin'])} · COGS {money(pl['cogs'])}")
        highlight('Operating costs', money(pl['total_costs'] - pl['cogs']), 'Expenses + payroll + supplies + write-offs')
        _finance(brief, business, start, end, section, highlight, money)
    elif allowed['expenses'] and not allowed['procurement']:
        from expenses.models import Expense
        expenses = Expense.objects.filter(business=business, date__range=(start, end))
        rows = list(expenses.values('category').annotate(value=Sum('amount'), count=Count('pk')).order_by('category'))
        total = sum((row['value'] or ZERO for row in rows), ZERO)
        section('Expenses · selected period', [f"{pretty(r['category'])}: {money(r['value'])} · {r['count']} records" for r in rows])
        highlight('Expenses', money(total), 'Expenses incurred in selected period')

    if allowed['inventory']:
        from inventory.models import RawMaterial, FinishedGood
        raws = list(RawMaterial.objects.filter(business=business).order_by('name'))
        goods = list(FinishedGood.objects.filter(business=business).select_related('portion_profile').prefetch_related('market_stock_lots').order_by('name'))
        for title, records, unit_field in [('Material stock', raws, 'usage_unit'), ('Product stock', goods, 'unit')]:
            lines, risks = [], []
            for item in records:
                unit = getattr(item, unit_field) or 'unit not set'
                status = stock_status(item)
                market = item.market_stock if unit_field == 'unit' and business.uses_production else ZERO
                expired = item.expired_market_stock if unit_field == 'unit' and business.uses_production else ZERO
                if market:
                    lines.append(f'🟢 OK · {item.name} · Market Stock: {format_product_quantity(market,item)}')
                if expired:
                    lines.append(f'🔴 EXPIRED · {item.name} · Market Stock: {format_product_quantity(expired,item)}')
                    if status not in {'OUT OF STOCK','LOW'}:
                        risks.append(f'{item.name}: {format_product_quantity(expired,item)} expired')
                if item.stock is None:
                    continue
                quantity = material_stock_label(item) if unit_field == 'usage_unit' else format_product_quantity(item.stock,item)
                if market or expired:
                    quantity += ' · Physical Store'
                badge = {'OK': '🟢', 'WARNING': '🟡', 'LOW': '🔴', 'OUT OF STOCK': '⚪'}.get(status, '⚪')
                lines.append(f'{badge} {status} · {item.name}: {quantity}')
                if status in {'OUT OF STOCK', 'LOW'}:
                    risks.append(f'{item.name}: {quantity}')
            section(title + ' · current position', lines)
            if not risks:
                continue
            highlight(title + ' alerts', f'{len(risks)} / {len(records)}',
                      'Needs attention: ' + ', '.join(risks[:3]) + (f' +{len(risks)-3} more' if len(risks) > 3 else '') if risks else 'No low or out-of-stock items')

    if allowed['inventory'] or (business.uses_production and allowed['production']):
        from inventory.product_use import product_issues
        issues = list(product_issues(business, start, end)) if allowed['inventory'] else []
        lines = []
        cost = sum((-issue.value for issue in issues), ZERO)
        for issue in issues:
            lines.append(f'{issue.get_reason_display()} · {issue.recipient or "Recipient not recorded"} · {issue.date:%d %b %Y} · {issue.get_stock_source_display()}')
            lines.append(f'  {issue.finished_good.name}: {product_quantity(-issue.quantity,issue.finished_good_id,issue.finished_good.unit)}' + (f' · Inventory cost: {money(-issue.value)}' if issue.value else ''))
            if issue.description and issue.description != f'{issue.get_reason_display()} — {issue.recipient}':
                lines.append(f'  Note: {issue.description}')
        production_use = []
        if business.uses_production and allowed['production']:
            from .brief_activity import non_sales_production
            production_use = list(non_sales_production(business, start, end))
            for row in production_use:
                lines.append(f'Non-sales production · {row["purpose"]} · {row["date"]:%d %b %Y}')
                lines.append(f'  {row["product"]}: {product_quantity(row["quantity"],row["product_id"],row["unit"])}' + (f' · Production cost: {money(row["cost"])}' if row['cost'] else ''))
        if cost:
            lines.append(f'Staff and gifting inventory cost: {money(cost)} (included in stock costs)')
        section('Staff and gifting · selected period', lines)
        if issues or production_use:
            highlight('Staff and gifting', f'{len(issues)+len(production_use)} records', f'Inventory cost: {money(cost)}' if cost else 'Non-sales product use')

    if business.uses_production and allowed['production']:
        from production.models import Order, ProductionBatch
        orders = Order.objects.filter(business=business, date__range=(start, end))
        pending = Order.objects.filter(business=business, status='pending').count()
        active = Order.objects.filter(business=business, status='approved').count()
        lines = []
        if pending: lines.append(f"Awaiting approval: {pending} orders")
        if active: lines.append(f"In production: {active} orders")
        lines += [f"Orders · {pretty(r['status'])}: {r['count']}" for r in orders.values('status').annotate(count=Count('pk')).order_by('status')]
        batches = ProductionBatch.objects.filter(business=business, production_date__range=(start, end), is_reversed=False)
        rows = batches.values('finished_good_id', 'finished_good__name', 'finished_good__unit').annotate(
            count=Count('pk'), planned=Sum('planned_units'), gross=Sum('produced_units'), waste=Sum('wastage_units'), cost=Sum('total_cost')).order_by('finished_good__name')
        for r in rows:
            gross, waste = r['gross'] or ZERO, r['waste'] or ZERO
            parts = [f"{label}: {product_quantity(amount,r['finished_good_id'],r['finished_good__unit'])}" for label,amount in [
                ('Produced',gross),('Planned',r['planned'] if r['planned'] != gross else ZERO),('Wastage',waste),('Saleable',max(ZERO,gross-waste) if waste else ZERO)] if amount]
            if r['cost']: parts.append(f"Production cost: {money(r['cost'])}")
            if parts: lines += [r['finished_good__name'], '  ' + ' · '.join(parts)]
        from production.variance import variance_report
        review = variance_report(business, timezone.localdate().replace(day=1), timezone.localdate())['totals']['review_count']
        if review: lines.append(f'Recipe reviews this month: {review}')
        section('Production · selected period', lines)
        attention = [(pending,'awaiting approval'),(active,'in production'),(review,'recipe reviews this month')]
        highlight('Production attention', str(pending+active+review), ' · '.join(f'{count} {label}' for count,label in attention if count))

    if allowed['procurement']:
        from procurement.models import PurchaseOrder, PurchaseOrderItem
        from procurement.services import request_count
        current = PurchaseOrder.objects.filter(business=business, status__in=('draft', 'ordered', 'approved'))
        received = PurchaseOrder.objects.filter(business=business, status='received', received_date__range=(start, end))
        value = ExpressionWrapper(F('qty') * F('unit_cost'), output_field=MONEY)
        received_value = PurchaseOrderItem.objects.filter(purchase_order__in=received).aggregate(value=Sum(value))['value'] or ZERO
        lines = [f'Received purchases: {money(received_value)} · {received.count()} orders'] if received_value else []
        if request_count(current): lines.append(f'Open purchase requests: {request_count(current)}')
        lines += [f"Open request #{po.pk} · {po.supplier or 'Unnamed supplier'} · {po.date:%d %b %Y} · {po.get_status_display()}" for po in current.order_by('date', 'pk')]
        vendors = {}
        for po in received.select_related('supplier_master').prefetch_related('items__raw_material', 'items__finished_good').order_by('supplier', 'pk'):
            vendor = vendors.setdefault(payee(po), {'total': ZERO, 'products': {}})
            for item in po.items.all():
                if not item.qty:
                    continue
                key = (item.item_name, item.stock_unit)
                product = vendor['products'].setdefault(key, [ZERO, ZERO])
                product[0] += item.qty
                product[1] += item.line_total
                vendor['total'] += item.line_total
        for name, vendor in vendors.items():
            if not vendor['products']:
                continue
            lines.append(f"Received from {name}: {money(vendor['total'])}")
            for (name, unit), (quantity, value) in sorted(vendor['products'].items()):
                lines.append(f'  {name}: {format_quantity(quantity, unit)}' + (f' · {money(value)}' if value else ''))
        if allowed['expenses'] or allowed['finance']:
            from expenses.models import Expense
            miscellaneous = list(Expense.objects.filter(business=business,date__range=(start,end),amount__gt=0).order_by('date','description'))
            if miscellaneous:
                lines += [f"Miscellaneous expenses: {money(sum((e.amount for e in miscellaneous),ZERO))}"]
                lines += [f"  {e.description}: {money(e.amount)}" for e in miscellaneous]
        from procurement.spending import period_totals
        spending = period_totals(business, start, end, expenses=allowed['expenses'] or allowed['finance'])
        if spending['purchases']:
            lines.append(f"Purchases entered in period: {money(spending['purchases'])} (includes pending requests)")
        if spending['grand_total']:
            lines.append(f"Purchases & expenses grand total: {money(spending['grand_total'])}")
        if spending['money_out']:
            lines.append(f"Actual money paid out in period: {money(spending['money_out'])}")
        highlight('Purchases & expenses', money(spending['grand_total']), f"Actual paid out: {money(spending['money_out'])}")
        section('Purchasing · selected period and current queue', lines)
        highlight('Open purchase requests', str(request_count(current)), f'Received in period: {money(received_value)}')

    for module, model_name, title in [('commerce', 'CommerceIntake', 'Commerce'), ('delivery', 'DeliveryAssignment', 'Delivery')]:
        if allowed[module]:
            from commerce import models
            qs = getattr(models, model_name).objects.filter(business=business, created_at__date__range=(start, end))
            section(title + ' · selected period', [f"{pretty(r['status'])}: {r['count']}" for r in qs.values('status').annotate(count=Count('pk')).order_by('status')])
    if allowed['ledger'] and ledger.report_scope_label(business):
        cutover = ledger.latest_cutover(business)
        section('Ledger controls · current', [f'Ledger scope: {str(ledger.current_scope() or "all").capitalize()}',
                f'Cut-over: {cutover.cutover_date} · {cutover.get_status_display()}' if cutover else 'No ledger split configured.'])
    if selected_sections is not None and 'distribution' in selected_sections:
        selected_sections = list(dict.fromkeys([key for key in selected_sections if key != 'distribution'] + ['sales']))
    choices = [{'key': key, 'label': SECTION_LABELS[key],
                'checked': selected_sections is None or key in selected_sections} for key in dict.fromkeys(available_sections)]
    if selected_sections is not None:
        selected = set(selected_sections) & set(SECTION_LABELS)
        sections = [item for item in sections if item['key'] in selected]
        highlights = [row for row in highlights if HIGHLIGHT_KEYS[row['label']] in selected]
        if 'profit' not in selected:
            brief.pop('executive_pl', None)
        if 'sales' not in selected:
            brief.pop('executive_sales_total', None)
    brief.update(report_permissions=allowed, executive_sections=sections, snapshot_highlights=select_highlights(highlights),
                 report_start=start, report_through=end, generated_at=timezone.localtime(),
                 report_options=choices, selected_sections=selected_sections)
    return brief


def margin(value):
    return 'not available' if value is None else f'{value:g}%'


def stock_status(item):
    if item.stock is None:
        return 'Not tracked in physical stock'
    if item.stock <= 0:
        return 'OUT OF STOCK'
    if item.is_low:
        return 'LOW'
    if item.is_warning:
        return 'WARNING'
    return 'OK'


def _finance(brief, business, start, end, section, highlight, money):
    from .finance_views import _cash_account_balances, _finance_open_items
    from .models import FinancialTransaction
    accounts = _cash_account_balances(business, active_only=True)
    opened = _finance_open_items(business, detail_limit=None)
    cash = sum((a._calculated_balance for a in accounts), ZERO)
    owing = {}
    for row in opened['outstanding_sales']:
        name = row['sale'].customer or 'Walk-in'
        owing[name] = owing.get(name, ZERO) + row['balance']
    brief['top_debtors'] = sorted(owing.items(), key=lambda item: -item[1])[:3]
    lines = [f'Cash on hand: {money(cash)}', f"Owed to you: {money(opened['receivables'])}",
             f"You owe: {money(opened['payables'])}", f"Working position (cash + receivables - payables): {money(cash+opened['receivables']-opened['payables'])}"]
    lines += [f'Account · {a.name}: {money(a._calculated_balance)}' for a in accounts]
    lines += [f"Customer balance · {r['sale'].customer or 'Walk-in'} · sale #{r['sale'].pk} · {r['sale'].date}: {money(r['balance'])}" for r in opened['outstanding_sales']]
    lines += [f"Supplier balance · {payee(r['po'])} · request #{r['po'].pk}: {money(r['balance'])}" for r in opened['outstanding_pos']]
    lines += [f"Unpaid expense · {r['expense'].description} · vendor {r['expense'].vendor or 'Vendor not recorded'}: {money(r['balance'])}" for r in opened['outstanding_expenses']]
    lines += [f"Unpaid walk-in issue (not a receivable) · sale #{r['sale'].pk} · {r['sale'].customer or 'Walk-in'}: {money(r['balance'])}" for r in opened['unpaid_invoice_sales']]
    creditors = {}
    for row in opened['outstanding_pos']:
        name = payee(row['po'])
        creditors[name] = creditors.get(name, ZERO) + row['balance']
    for row in opened['outstanding_expenses']:
        name = row['expense'].vendor or 'Vendor not recorded'
        creditors[name] = creditors.get(name, ZERO) + row['balance']
    brief['top_creditors'] = sorted(creditors.items(), key=lambda item: -item[1])
    lines += [f'Payee · {name}: {money(amount)}' for name, amount in brief['top_creditors']]
    section('Cash and outstanding balances · current position', lines)
    flows = FinancialTransaction.objects.filter(business=business, date__range=(start, end), reversed=False)
    totals = {r['transaction_type']: r['value'] or ZERO for r in flows.values('transaction_type').annotate(value=Sum('amount'))}
    incoming, outgoing = totals.get(FinancialTransaction.INCOME, ZERO), totals.get(FinancialTransaction.OUTFLOW, ZERO)
    lines = [f'Money in: {money(incoming)}', f'Money out: {money(outgoing)}', f'Net cash flow: {money(incoming-outgoing)}']
    lines += [f"{pretty(r['transaction_type'])} · {pretty(r['category'])}: {money(r['value'])}" for r in flows.values('transaction_type', 'category').annotate(value=Sum('amount')).order_by('transaction_type', 'category')]
    section('Cash movement · selected period', lines)
    highlight('Cash / account balances', money(cash), f'Period cash flow {money(incoming-outgoing)}')
    highlight('Receivables', money(opened['receivables']), 'Current customer balances')
    highlight('Payables', money(opened['payables']), '; '.join(name for name, _amount in brief['top_creditors'][:3]) or 'No outstanding supplier/vendor balances')




def full_text(brief):
    lines = [f"{brief['report_start']:%d %b %Y} – {brief['report_through']:%d %b %Y}",
             f"Stock and outstanding balances as of {brief['generated_at']:%d %b %Y %H:%M %Z}."]
    scope = ledger.report_scope_label(brief['business'])
    if scope:
        lines.append(f'Ledger scope: {scope}.')
    for section in brief['executive_sections']:
        lines += ['', f"*{section['title']}*"]
        lines += [bullet(line, level=(len(line)-len(line.lstrip()))//2) for line in section['lines']]
    if not brief['executive_sections']:
        lines += ['', 'No activity or balances to show for this selection.' if brief.get('selected_sections') != [] else 'No report sections selected. Choose what to include in Report contents.']
    return '\n'.join(lines)


def select_highlights(highlights):
    """Eight readable image rows; related financial metrics share a row."""
    by_label = {row['label']: dict(row) for row in highlights}
    selected = []
    sales = by_label.get('Recorded sales')
    revenue = by_label.get('Paid-sales revenue')
    if sales:
        if revenue:
            sales['detail'] = sales['detail'].split(' · ')[0] + ' · Paid revenue ' + revenue['value']
        selected.append(sales)
    elif revenue:
        selected.append(revenue)
    gross = by_label.get('Gross profit')
    costs = by_label.get('Operating costs')
    if gross:
        if costs:
            gross['detail'] += ' · Other costs ' + costs['value']
        selected.append(gross)
    elif costs:
        selected.append(costs)
    elif 'Expenses' in by_label:
        selected.append(by_label['Expenses'])
    if 'Cash / account balances' in by_label:
        selected.append(by_label['Cash / account balances'])
    receivables, payables = by_label.get('Receivables'), by_label.get('Payables')
    if receivables:
        if payables:
            receivables['detail'] = 'You owe ' + payables['value'] + ' · ' + payables['detail']
        selected.append(receivables)
    elif payables:
        selected.append(payables)
    for label in ['Material stock alerts', 'Product stock alerts']:
        if label in by_label:
            selected.append(by_label[label])
    if 'Purchases & expenses' in by_label and len(selected) < 8:
        selected.append(by_label['Purchases & expenses'])
    operations = [by_label[label] for label in ['Market Stock shelf life', 'Production attention', 'Open purchase requests'] if label in by_label]
    # Show actual exceptions first; a quiet shelf-life indicator should not
    # displace an outstanding approval or purchasing queue.
    operations.sort(key=lambda row: Decimal(row['value']) > 0, reverse=True)
    if 'Staff and gifting' in by_label:
        selected.append(by_label['Staff and gifting'])
    selected.extend(operations[:max(0, 8-len(selected))])
    return selected[:8]


def payee(po):
    return po.supplier or (po.supplier_master.name if po.supplier_master_id else 'Supplier not recorded')


def compact_lines(lines):
    import re
    out = []
    for line in lines:
        parts = []
        for part in line.split(' · '):
            value = part.rsplit(': ',1)[-1] if ': ' in part else part
            if ': ' not in part and any(char.isdigit() for char in part) and not re.match(r'^[-+]?\d',part):
                parts.append(part); continue
            # Counts and formatted monetary values only; stock rows retain zero
            # when it identifies an out-of-stock item.
            if part.startswith(('🟢','🟡','🔴','⚪')):
                parts.append(part); continue
            if re.fullmatch(r'[^\d]*[+-]?[\d,]+(?:\.\d+)?(?:%| sales| orders| records)?', value) and not has_value(value):
                continue
            parts.append(part)
        if parts: out.append(' · '.join(parts))
    return out
