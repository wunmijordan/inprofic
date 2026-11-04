"""Full executive report text and deliberately bounded image highlights.

Period flows and current positions are labelled separately. No stock or financial
records are changed, and quantities in different registered units never combine.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, ExpressionWrapper, F, Sum
from django.utils import timezone

from accounts.services import user_has_permission
from . import ledger
from .accounting import _sale_expressions, profit_and_loss
from .complete_summaries import MONEY, num, pretty
from .daily_brief import _money

ZERO = Decimal('0')
SECTION_LABELS = {
    'sales': 'Sales and customer breakdown', 'profit': 'Profit and loss',
    'expenses': 'Expenses', 'position': 'Cash accounts and who owes / is owed',
    'cash_flow': 'Money in and money out', 'materials': 'Material stock levels',
    'products': 'Product stock levels', 'distribution': 'Distribution and returns',
    'production': 'Production and approval queue', 'purchasing': 'Purchasing and open requests',
    'commerce': 'Commerce activity', 'delivery': 'Delivery activity', 'ledger': 'Ledger controls',
}
TITLE_KEYS = {
    'Sales activity': 'sales', 'Profit and loss': 'profit', 'Expenses': 'expenses',
    'Cash and outstanding balances': 'position', 'Cash movement': 'cash_flow',
    'Material stock': 'materials', 'Product stock': 'products',
    'Distribution': 'distribution', 'Recorded distribution returns': 'distribution',
    'Production': 'production', 'Purchasing': 'purchasing',
    'Commerce': 'commerce', 'Delivery': 'delivery', 'Ledger controls': 'ledger',
}
HIGHLIGHT_KEYS = {
    'Recorded sales': 'sales', 'Paid-sales revenue': 'profit', 'Gross profit': 'profit',
    'Operating costs': 'profit', 'Cash / account balances': 'position', 'Receivables': 'position',
    'Payables': 'position', 'Material stock alerts': 'materials', 'Product stock alerts': 'products',
    'Market Stock shelf life': 'distribution', 'Production attention': 'production',
    'Open purchase requests': 'purchasing', 'Expenses': 'expenses',
}


def enrich(brief, start, end, *, user=None, selected_sections=None):
    """Add complete, permission-scoped sections without changing legacy tool fields."""
    business = brief['business']
    allowed = {module: user is None or user_has_permission(user, business, module, 'view')
               for module in ('sales', 'finance', 'expenses', 'inventory', 'production', 'procurement', 'commerce', 'delivery', 'ledger')}
    sections, highlights = [], []
    money = lambda value: _money(business, value)

    def section(title, lines):
        key = TITLE_KEYS[title.split(' · ')[0]]
        sections.append({'key': key, 'title': title, 'lines': lines or ['No records.']})

    def highlight(label, value, detail=''):
        highlights.append({'label': label, 'value': str(value), 'detail': detail})

    if allowed['sales']:
        from sales.models import Sale, SaleItem
        sales = Sale.objects.filter(business=business, date__range=(start, end))
        items = SaleItem.objects.filter(sale__in=sales)
        _field, line, _cogs, units = _sale_expressions(prefix='')
        total = items.aggregate(value=Sum(line))['value'] or ZERO
        count = sales.count()
        lines = [f"Sales: {money(total)} from {count} sale{'s' if count != 1 else ''}",
                 f"Average sale: {money(total / count if count else ZERO)}"]
        for field, label in [('sale__source', 'Channel'), ('sale__transaction_type', 'Payment status')]:
            rows = items.values(field).annotate(value=Sum(line), count=Count('sale_id', distinct=True)).order_by(field)
            lines += [f"{label} · {Sale(business=business, source=r[field]).display_source if field == 'sale__source' else pretty(r[field])}: {r['count']} sales · {money(r['value'])}" for r in rows]
        rows = items.values('finished_good__name', 'finished_good__unit').annotate(value=Sum(line), quantity=Sum(units)).order_by('-value', 'finished_good__name')
        lines += [f"Product · {r['finished_good__name']}: {num(r['quantity'])} {r['finished_good__unit']} · sales {money(r['value'])}" for r in rows]
        rows = items.values('sale__customer').annotate(value=Sum(line), count=Count('sale_id', distinct=True)).order_by('-value', 'sale__customer')
        lines += [f"Customer · {r['sale__customer'] or 'Walk-in'}: {r['count']} sales · {money(r['value'])}" for r in rows]
        section('Sales activity · selected period', lines)
        highlight('Recorded sales', money(total), f'{count} sales · includes unpaid / part-paid')
        brief['executive_sales_total'] = total

    if allowed['finance']:
        pl = brief.get('pl') or profit_and_loss(business, start, end)
        brief['executive_pl'] = pl
        lines = [f"Revenue (fully paid sales): {money(pl['revenue'])}", f"Cost of goods: {money(pl['cogs'])}",
                 f"Gross profit: {money(pl['gross_profit'])} · margin {margin(pl['gross_margin'])}",
                 f"Operating expenses: {money(pl['operating_expenses'])}"]
        lines += [f"Expense category · {r['label']}: {money(r['amount'])}" for r in pl['expense_lines']]
        lines += [f"Payroll: {money(pl['payroll'])}", f"Operational stock supplies: {money(pl['supplies'])}",
                  f"Stock write-offs: {money(pl['write_offs'])}", f"Total costs: {money(pl['total_costs'])}",
                  f"Net profit / loss: {money(pl['net_profit'])} · margin {margin(pl['net_margin'])}",
                  f"Unpaid sales not recognised as revenue: {money(pl['unpaid_sales'])}",
                  f"Unpaid expenses included above: {money(pl['unpaid_expenses'])}",
                  'Basis: Finance P&L recognises fully paid sales and expenses when incurred; Books can differ.']
        if 'revenue_change' in brief:
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
    elif allowed['expenses']:
        from expenses.models import Expense
        expenses = Expense.objects.filter(business=business, date__range=(start, end))
        rows = list(expenses.values('category').annotate(value=Sum('amount'), count=Count('pk')).order_by('category'))
        total = sum((row['value'] or ZERO for row in rows), ZERO)
        section('Expenses · selected period', [f"{pretty(r['category'])}: {money(r['value'])} · {r['count']} records" for r in rows])
        highlight('Expenses', money(total), 'Expenses incurred in selected period')

    if allowed['inventory']:
        from inventory.models import RawMaterial, FinishedGood
        raws = list(RawMaterial.objects.filter(business=business).order_by('name'))
        goods = list(FinishedGood.objects.filter(business=business).order_by('name'))
        for title, records, unit_field in [('Material stock', raws, 'usage_unit'), ('Product stock', goods, 'unit')]:
            lines, risks = [], []
            for item in records:
                unit = getattr(item, unit_field) or 'unit not set'
                status = stock_status(item)
                quantity = 'Not held in physical stock' if item.stock is None else f'{num(item.stock)} {unit}'
                threshold = 'not configured' if not item.reorder_level else f'{num(item.reorder_level)} {unit}'
                lines.append(f'{item.name}: {quantity} · reorder at {threshold} · {status}')
                if status in {'OUT OF STOCK', 'LOW'}:
                    risks.append(f'{item.name} {num(item.stock)} {unit}')
            section(title + ' · current position', lines)
            highlight(title + ' alerts', f'{len(risks)} / {len(records)}',
                      'Low/out: ' + ', '.join(risks[:3]) + (f' +{len(risks)-3} more' if len(risks) > 3 else '') if risks else 'No low or out-of-stock items')
        if business.uses_production:
            _market(business, start, end, section, highlight, money)
        else:
            from inventory.models import DistributionReturn
            returned = DistributionReturn.objects.filter(business=business, date__range=(start, end)).select_related('sale_item__sale', 'sale_item__finished_good')
            section('Recorded distribution returns · selected period', [f'{r.date} · {r.sale_item.sale.customer} · {r.sale_item.finished_good.name}: {num(r.quantity)} {r.sale_item.finished_good.unit} · {r.get_condition_display()}' for r in returned])

    if business.uses_production and allowed['production']:
        from production.models import Order, ProductionBatch
        orders = Order.objects.filter(business=business, date__range=(start, end))
        pending = Order.objects.filter(business=business, status='pending').count()
        active = Order.objects.filter(business=business, status='approved').count()
        lines = [f"{pending} order{'s' if pending != 1 else ''} awaiting approval (current)", f'In production (approved, current): {active}']
        lines += [f"Orders · {pretty(r['status'])}: {r['count']}" for r in orders.values('status').annotate(count=Count('pk')).order_by('status')]
        batches = ProductionBatch.objects.filter(business=business, production_date__range=(start, end), is_reversed=False)
        rows = batches.values('finished_good__name', 'finished_good__unit').annotate(
            count=Count('pk'), planned=Sum('planned_units'), gross=Sum('produced_units'), waste=Sum('wastage_units'), cost=Sum('total_cost')).order_by('finished_good__name')
        for r in rows:
            gross, waste = r['gross'] or ZERO, r['waste'] or ZERO
            lines.append(f"{r['finished_good__name']}: {r['count']} batches · planned {num(r['planned'])} · gross {num(gross)} · wastage {num(waste)} · saleable {num(max(ZERO, gross-waste))} {r['finished_good__unit']} · cost {money(r['cost'])}")
        from production.variance import variance_report
        review = variance_report(business, timezone.localdate().replace(day=1), timezone.localdate())['totals']['review_count']
        lines.append(f'{review} ingredients off recipe this month (current month)')
        section('Production · selected period', lines)
        highlight('Production attention', str(pending), f'{active} in production · {review} recipe reviews this month')

    if allowed['procurement']:
        from procurement.models import PurchaseOrder, PurchaseOrderItem
        from procurement.services import request_count
        current = PurchaseOrder.objects.filter(business=business, status__in=('draft', 'ordered', 'approved'))
        received = PurchaseOrder.objects.filter(business=business, status='received', received_date__range=(start, end))
        value = ExpressionWrapper(F('qty') * F('unit_cost'), output_field=MONEY)
        received_value = PurchaseOrderItem.objects.filter(purchase_order__in=received).aggregate(value=Sum(value))['value'] or ZERO
        lines = [f'Received purchases: {received.count()} · {money(received_value)}', f'Open purchase requests (current): {request_count(current)}']
        lines += [f"Open request #{po.pk} · {po.supplier or 'Unnamed supplier'} · {po.date:%d %b %Y} · {po.get_status_display()}" for po in current.order_by('date', 'pk')]
        suppliers = received.values('supplier').annotate(count=Count('pk')).order_by('supplier')
        lines += [f"Received from {r['supplier'] or 'Unnamed supplier'}: {r['count']} orders" for r in suppliers]
        section('Purchasing · selected period and current queue', lines)
        highlight('Open purchase requests', str(request_count(current)), f'Received in period: {money(received_value)}')

    for module, model_name, title in [('commerce', 'CommerceIntake', 'Commerce'), ('delivery', 'DeliveryAssignment', 'Delivery')]:
        if allowed[module]:
            from commerce import models
            qs = getattr(models, model_name).objects.filter(business=business, created_at__date__range=(start, end))
            section(title + ' · selected period', [f"{pretty(r['status'])}: {r['count']}" for r in qs.values('status').annotate(count=Count('pk')).order_by('status')])
    if allowed['ledger']:
        cutover = ledger.latest_cutover(business)
        section('Ledger controls · current', [f'Ledger scope: {str(ledger.current_scope() or "all").capitalize()}',
                f'Cut-over: {cutover.cutover_date} · {cutover.get_status_display()}' if cutover else 'No ledger split configured.'])
    choices = [{'key': item['key'], 'label': SECTION_LABELS[item['key']],
                'checked': selected_sections is None or item['key'] in selected_sections} for item in sections]
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


def _market(business, start, end, section, highlight, money):
    from inventory.models import MarketStockLot, MarketStockMovement, DistributionReturn
    today = timezone.localdate()
    lots = list(MarketStockLot.objects.filter(business=business, active=True, quantity_available__gt=0).select_related('finished_good').order_by('finished_good__name', 'expiry_date', 'pk'))
    lines = [f"{lot.finished_good.name} · lot #{lot.pk}: {num(lot.quantity_available)} {lot.finished_good.unit} · expires {lot.expiry_date or 'not set'} · {'EXPIRED / BLOCKED' if lot.is_expired else 'sellable'}" for lot in lots]
    movements = MarketStockMovement.objects.filter(business=business, date__range=(start, end)).select_related('lot__finished_good', 'customer', 'sale').order_by('date', 'pk')
    for movement in movements:
        target = movement.sale.customer if movement.sale else (movement.customer.name if movement.customer else '')
        if movement.movement_type == MarketStockMovement.TRANSFER_PHYSICAL:
            target = 'Physical Store'
        lines.append(f'{movement.date} · {movement.lot.finished_good.name} · {movement.get_movement_type_display()}: {num(movement.quantity)} {movement.lot.finished_good.unit}' + (f' · {target}' if target else ''))
    returned = DistributionReturn.objects.filter(business=business, date__range=(start, end)).select_related('sale_item__sale', 'sale_item__finished_good')
    lines += [f'{r.date} · recorded return · {r.sale_item.sale.customer} · {r.sale_item.finished_good.name}: {num(r.quantity)} {r.sale_item.finished_good.unit} · {r.get_condition_display()} · write-off {money(r.writeoff_value)}' for r in returned]
    section('Distribution · current lots and period movements', lines)
    blocked = sum(1 for lot in lots if lot.is_expired)
    soon = sum(1 for lot in lots if lot.expiry_date and today <= lot.expiry_date <= today+timedelta(days=7))
    highlight('Market Stock shelf life', str(blocked), f'{blocked} blocked lots · {soon} expire within 7 days')


def full_text(brief):
    lines = [f"Period activity: {brief['report_start']:%d %b %Y} – {brief['report_through']:%d %b %Y}.",
             f"Current positions as of {brief['generated_at']:%d %b %Y %H:%M %Z}; these are not historical closing balances.",
             f"Ledger scope: {str(ledger.current_scope() or 'all').capitalize()}. Sections follow the sender's access."]
    for section in brief['executive_sections']:
        lines += ['', f"*{section['title']}*"]
        lines += ['• ' + line for line in section['lines']]
    if not brief['executive_sections']:
        lines += ['', 'No report sections selected. Choose what to include in Report contents.']
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
    operations = [by_label[label] for label in ['Market Stock shelf life', 'Production attention', 'Open purchase requests'] if label in by_label]
    # Show actual exceptions first; a quiet shelf-life indicator should not
    # displace an outstanding approval or purchasing queue.
    operations.sort(key=lambda row: Decimal(row['value']) > 0, reverse=True)
    selected.extend(operations[:max(0, 8-len(selected))])
    return selected[:8]


def payee(po):
    return po.supplier or (po.supplier_master.name if po.supplier_master_id else 'Supplier not recorded')
