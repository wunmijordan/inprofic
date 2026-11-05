"""Wake device alerts only after a stock/settings mutation has committed."""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import RawMaterial, FinishedGood, StockMovement, InventoryAlertSettings


@receiver([post_save, post_delete], sender=RawMaterial, dispatch_uid='inventory_push_material')
@receiver([post_save, post_delete], sender=FinishedGood, dispatch_uid='inventory_push_product')
@receiver([post_save, post_delete], sender=StockMovement, dispatch_uid='inventory_push_movement')
@receiver(post_save, sender=InventoryAlertSettings, dispatch_uid='inventory_push_settings')
def schedule_stock_alerts(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from commerce.webpush import schedule_push_dispatch
    schedule_push_dispatch()
