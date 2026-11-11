"""Read-only product activity used in daily and weekly executive text."""
from collections import defaultdict
from django.db.models import Q, Prefetch

from .quantities import format_product_quantity


def customer_return_lines(business, start, end):
    """Group returns and known onward movements under the original customer.

    Return outcomes, like invoice settlement, include later recorded activity.
    A transfer to shelf stock is reported as a transfer, never inferred as a sale.
    """
    from inventory.models import DistributionReturn, MarketStockMovement
    from sales.models import Sale
    movements = MarketStockMovement.raw_objects.filter(business=business).select_related('sale').order_by('date','pk')
    returns = DistributionReturn.objects.filter(business=business).filter(
        Q(date__range=(start, end)) | Q(sale_item__sale__business=business, sale_item__sale__date__range=(start,end)) | Q(market_lot__movements__business=business,
            market_lot__movements__date__range=(start, end), market_lot__movements__quantity__lt=0)
    ).distinct().select_related('sale_item__sale', 'sale_item__finished_good__portion_profile', 'market_lot').prefetch_related(
        Prefetch('market_lot__movements', queryset=movements))
    customers = defaultdict(list)
    for returned in returns.order_by('sale_item__sale__customer', 'date', 'pk'):
        item = returned.sale_item
        if item.sale.business_id != business.pk or item.finished_good.business_id != business.pk or (returned.market_lot and returned.market_lot.business_id != business.pk):
            continue
        lines = customers[item.sale.customer or '']
        lines.append(f'  Returned · {item.finished_good.name}: {format_product_quantity(returned.quantity,item.finished_good)} · {returned.date:%d %b %Y}')
        if returned.condition == DistributionReturn.DAMAGED:
            lines.append('    Damaged / unsellable · removed from customer balance')
            continue
        lot = returned.market_lot
        if not lot:
            lines.append('    Suitable for redistribution · destination not linked')
            continue
        for movement in lot.movements.all():
            if movement.quantity >= 0:
                continue
            amount = format_product_quantity(-movement.quantity,item.finished_good)
            if movement.movement_type == MarketStockMovement.RELEASE:
                sale = movement.sale if movement.sale and movement.sale.business_id == business.pk else None
                if sale and sale.source == 'walkin':
                    label = 'Sold in ' + Sale(business=business, source=sale.source).display_source
                elif sale and sale.source == 'online_order':
                    label = f'Online sale · {sale.customer or "Customer not named"}'
                else:
                    label = f'Redistributed to {sale.customer if sale else "customer not linked"}'
            elif movement.movement_type == MarketStockMovement.NON_STOCK:
                label = movement.note.split('; ADJ-', 1)[0] or 'Staff / gifting issue'
            elif movement.movement_type == MarketStockMovement.TRANSFER_PHYSICAL:
                label = 'Transferred to Physical Store'
            elif movement.movement_type == MarketStockMovement.EXPIRY:
                label = 'Written off as expired'
            elif movement.movement_type == MarketStockMovement.REVERSAL:
                label = 'Stock reversal'
            else:
                label = movement.get_movement_type_display()
            lines.append(f'    {label}: {amount} · {movement.date:%d %b %Y}')
        if lot.quantity_available:
            lines.append(f'    Remaining in Market Stock: {format_product_quantity(lot.quantity_available,item.finished_good)}')
    return customers


def non_sales_production(business, start, end):
    """Recorded non-stock output, including split allocations and excess.

    Positive completion movements are canonical for delivered quantities. The
    batch filter drops reversed completions without guessing from free text.
    """
    from inventory.models import StockMovement
    from production.models import ProductionBatch
    batches = ProductionBatch.objects.filter(business=business,
        production_date__range=(start,end), is_reversed=False)
    batch_dates = dict(batches.values_list('batch_number','production_date'))
    rows = StockMovement.objects.filter(business=business, finished_good__business=business, reference__in=batch_dates,
        affects_stock=False, movement_type=StockMovement.FG_PRODUCTION, quantity__gt=0).filter(
            Q(note__startswith='Non-stock production (') | Q(note__startswith='Excess production for ')
        ).select_related('finished_good').order_by('business_date', 'pk')
    for row in rows:
        purpose = row.note.split(' — batch ',1)[0]
        if purpose.startswith('Non-stock production ('):
            purpose = purpose.removeprefix('Non-stock production (').removesuffix(')')
        else:
            purpose = purpose.removeprefix('Excess production for ')
        yield {'product': row.finished_good.name, 'product_id': row.finished_good_id, 'unit': row.finished_good.unit, 'quantity': row.quantity,
               'purpose': purpose, 'date': row.business_date or batch_dates[row.reference],
               'cost': row.quantity * row.unit_value}
