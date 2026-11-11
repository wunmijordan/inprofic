"""Read-only period views of products issued to staff or given away."""
from .models import StockAdjustment


def product_issues(business, start, end, *, stock_source=None):
    rows = StockAdjustment.objects.filter(business=business, date__range=(start, end),
        finished_good__isnull=False, quantity__lt=0, reason__in=['staff', 'charity'], reversed=False)
    if stock_source:
        rows = rows.filter(stock_source=stock_source)
    return rows.select_related('finished_good__portion_profile', 'created_by').order_by('date', 'pk')
