"""Authenticated report snapshots using the shared light paper export layout."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_GET
from django.utils import timezone
from accounts.services import user_has_permission
from . import daily_brief, ledger
from .brief_views import _report, report_dashboard_url
from .snapshot_images import render_report_snapshot, snapshot_response


@login_required
@require_GET
def brief_snapshot(request):
    if not user_has_permission(request.user, request.business, 'dashboard', 'view'):
        raise PermissionDenied
    report = _report(request.business, request.GET, user=request.user)
    brief, business = report['brief'], request.business
    money = lambda value: daily_brief._money(business, value)
    scope = ledger.report_scope_label(business)
    # A single image has a fixed editorial budget. Complete details remain in
    # the identical text payload used for sharing and copying.
    rows = brief['snapshot_highlights']
    pl = brief.get('executive_pl')
    if pl is not None and pl['net_profit']:
        total_label = 'Net loss' if pl['net_profit'] < 0 else 'Net profit'
        total = money(pl['net_profit'])
    elif brief.get('executive_sales_total'):
        total_label, total = 'Recorded sales', money(brief['executive_sales_total'])
    else:
        total_label, total = '', None
    footer = 'Period activity; balances and stock are current · Full details in text'
    content = render_report_snapshot(
        business=business, title='Weekly Summary' if report['view']=='weekly' else 'Daily Brief',
        subtitle=report['label'], rows=rows, total_label=total_label, total=total, footer=footer,
        url=report_dashboard_url(request,report), link_heading='Open INPROFIC for the complete dashboard',
        max_rows=8, scope=scope, stamp='As of '+timezone.localtime().strftime('%d %b %H:%M'))
    return snapshot_response(content, report['view']+'-summary')
