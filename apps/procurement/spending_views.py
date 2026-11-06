"""Permission-scoped exports and sharing for the purchases/expenses workspace."""
import csv
import io
from decimal import Decimal
from urllib.parse import urlencode
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_GET
from core.seo import absolute_url
from core.snapshot_images import render_report_snapshot, snapshot_response
from .spending import workspace_period, workspace_records


def period_query(context):
    return urlencode({key: value.isoformat() for key, value in (
        ('start', context.get('period_start')), ('end', context.get('period_end'))) if value})


def period_label(context):
    start, end = context.get('period_start'), context.get('period_end')
    if start and end:
        return f'{start:%d %b %Y} – {end:%d %b %Y}'
    if start:
        return f'From {start:%d %b %Y}'
    if end:
        return f'Through {end:%d %b %Y}'
    return 'All dates in this workspace'


def summary_text(request, context, url):
    money = lambda amount: f'{request.business.currency_symbol}{amount:,.2f}'
    totals = context['spending_totals']
    lines = ['*Purchases & expenses*', request.business.name, period_label(context), '']
    for row in context['spending_activity']:
        if row['kind'] == 'purchase':
            po = row['order']
            lines += [f'*Purchase request #{po.pk} · {row["date"]:%d %b %Y}*',
                f'{row["usage"]} · {po.request_status_label} · Payment: {row["payment"]}']
            for vendor in po.request_orders:
                lines.append(vendor.supplier or 'Vendor not named')
                for item in vendor.items.all():
                    if item.qty:
                        from core.quantities import format_quantity
                        lines.append(f'• {item.item_name}: {format_quantity(item.qty, item.stock_unit)} × {money(item.unit_cost)} = {money(item.line_total)}')
                lines.append(f'Group total: {money(vendor.total)}')
                paid = vendor.total - vendor.payment_balance
                if paid:
                    lines.append(f'Paid: {money(paid)} · {vendor.get_payment_method_display()}' + (f' · {vendor.account}' if vendor.account_id else ''))
                if vendor.payment_balance:
                    lines.append(f'Balance: {money(vendor.payment_balance)}')
            lines.append(f'Purchase total: {money(row["amount"])}')
        else:
            expense = row['expense']
            lines += [f'*General-use expense · {row["date"]:%d %b %Y}*',
                f'• {expense.description}: {money(expense.amount)}',
                f'{expense.get_category_display()} · {row["vendor"]} · {row["payment"]}']
        lines.append('')
    for key, label in [('purchases', 'Materials / stock purchases'), ('expenses', 'General-use expenses'),
                       ('grand_total', 'Grand total'), ('money_out', 'Actual money paid out')]:
        if totals[key]:
            lines.append(f'*{label}: {money(totals[key])}*')
    lines += ['', 'Grand total includes paid and unpaid entries. Money paid out uses cash records dated in this period, including payments for earlier purchases.',
              '', 'Open full purchases & expenses:', url]
    return '\n'.join(lines)


def share_context(request, context):
    query = period_query(context)
    suffix = '?' + query if query else ''
    url = absolute_url(request, reverse('procurement_list') + suffix)
    return {'spending_period_label': period_label(context), 'spending_query': query,
        'spending_image_url': reverse('spending_snapshot') + suffix,
        'spending_share_text': summary_text(request, context, url)}


@login_required
@require_GET
def spending_snapshot(request):
    context = workspace_period(request)
    if not context['period_valid']:
        return HttpResponse('Choose a valid date range.', status=400)
    query = period_query(context)
    totals = context['spending_totals']
    money = lambda amount: f'{request.business.currency_symbol}{amount:,.2f}'
    rows = [{'label': label, 'value': money(totals[key]), 'detail': detail} for key, label, detail in (
        ('purchases', 'Materials / stock purchases', 'Purchase requests dated in this period, including pending entries'),
        ('expenses', 'General-use expenses', 'Includes utilities, repairs and other general spending'),
        ('money_out', 'Actual money paid out', 'Cash payments in this period, net of corrections')) if totals[key]]
    content = render_report_snapshot(business=request.business, title='Purchases & expenses',
        subtitle=period_label(context), rows=rows, total_label='Grand total', total=money(totals['grand_total']),
        footer='Purchase and expense value, including unpaid entries',
        url=absolute_url(request, reverse('procurement_list') + ('?' + query if query else '')),
        link_heading='Open full purchases & expenses')
    return snapshot_response(content, 'purchases-and-expenses.png')


@login_required
@require_GET
def spending_export(request, file_format):
    context = workspace_records(request)
    if not context['period_valid']:
        return HttpResponse('Choose a valid date range.', status=400)
    headers = ['Date', 'Type', 'Vendor', 'Description', 'Value', 'Payment']
    rows = [[row['date'].isoformat(), row['usage'] if row['kind'] == 'purchase' else 'General-use expense',
             row['vendor'], row['description'], str(row['amount']), row['payment']] for row in context['spending_activity']]
    totals = context['spending_totals']
    rows += [['', '', '', label, str(totals[key]), ''] for key, label in (
        ('grand_total', 'Grand total'), ('money_out', 'Actual money paid out'))]
    if file_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        writer = csv.writer(response)
        # Spreadsheet formulas must never execute text supplied as vendor/item names.
        for row in [headers] + rows:
            writer.writerow(["'" + value if index != 4 and isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value for index, value in enumerate(row)])
    else:
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Purchases and expenses'
        for row in [headers] + rows:
            sheet.append(row)
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        for index, row in enumerate(rows, 2):
            sheet.cell(index, 5).value = Decimal(row[4])
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        stream = io.BytesIO()
        workbook.save(stream)
        response = HttpResponse(stream.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="purchases-and-expenses.{file_format}"'
    response['Cache-Control'] = 'private, no-store'
    return response
