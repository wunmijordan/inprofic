from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from core.models import BusinessOwnedModel, TimestampedModel


class PurchaseOrder(BusinessOwnedModel):
    STATUS_CHOICES = [("draft", "Draft"), ("ordered", "Ordered"), ("received", "Received")]
    PAYMENT_STATUS_CHOICES = [("paid", "Paid"), ("partial", "Partially Paid"), ("unpaid", "Unpaid")]
    PAYMENT_METHOD_CHOICES = [("Cash", "Cash"), ("Card", "Card"), ("Transfer", "Transfer")]

    date = models.DateField()
    supplier = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="draft")
    received_date = models.DateField(null=True, blank=True)
    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATUS_CHOICES, default="paid")
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, default="Transfer")
    account = models.ForeignKey("core.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_orders")

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["business", "status", "payment_status"], name="po_biz_status_pay_idx"),
            models.Index(fields=["business", "date"], name="po_biz_date_idx"),
            models.Index(fields=["business", "status", "received_date"], name="po_biz_status_recv_idx"),
        ]

    def __str__(self):
        return f"PO #{self.id} — {self.supplier or 'Unnamed supplier'}"

    @property
    def total(self):
        return sum((i.qty * i.unit_cost for i in self.items.all()), Decimal("0"))


class PurchaseOrderItem(TimestampedModel):
    purchase_order = models.ForeignKey(PurchaseOrder, related_name="items", on_delete=models.CASCADE)
    raw_material = models.ForeignKey(
        "inventory.RawMaterial", null=True, blank=True, on_delete=models.PROTECT,
        related_name="purchase_order_items",
    )
    finished_good = models.ForeignKey(
        "inventory.FinishedGood", null=True, blank=True, on_delete=models.PROTECT,
        related_name="purchase_order_items",
    )
    qty = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(raw_material__isnull=False, finished_good__isnull=True)
                    | models.Q(raw_material__isnull=True, finished_good__isnull=False)
                ),
                name="purchase_item_has_exactly_one_stock_item",
            ),
        ]

    def clean(self):
        super().clean()
        if self.qty is not None and self.qty <= 0:
            raise ValidationError("Purchase quantity must be greater than zero.")
        if self.unit_cost is not None and self.unit_cost < 0:
            raise ValidationError("Purchase unit cost cannot be negative.")
        if bool(self.raw_material_id) == bool(self.finished_good_id):
            raise ValidationError("Select exactly one material or stock product.")
        item = self.raw_material or self.finished_good
        if (
            item
            and self.purchase_order_id
            and item.business_id != self.purchase_order.business_id
        ):
            raise ValidationError("The selected item must belong to the purchase order's business.")

    @property
    def stock_item(self):
        return self.raw_material or self.finished_good

    @property
    def item_name(self):
        return self.stock_item.name

    @property
    def item_type(self):
        return "Material" if self.raw_material_id else "Product for resale"

    @property
    def item_category(self):
        return self.raw_material.get_category_display() if self.raw_material_id else "Product for resale"

    @property
    def stock_unit(self):
        return self.raw_material.purchase_unit if self.raw_material_id else self.finished_good.unit

    @property
    def item_identity(self):
        return ("raw", self.raw_material_id) if self.raw_material_id else ("finished", self.finished_good_id)

    @property
    def line_total(self):
        return self.qty * self.unit_cost


class RawMaterialCostSnapshot(BusinessOwnedModel):
    """Historical procurement price that becomes available when a purchase is received.

    Production costing uses the latest received snapshot at the production date;
    it never averages older procurement prices into the production cost.
    """
    raw_material = models.ForeignKey("inventory.RawMaterial", related_name="cost_snapshots", on_delete=models.PROTECT)
    purchase_order_item = models.ForeignKey(PurchaseOrderItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="cost_snapshots")
    effective_date = models.DateField()
    purchase_unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    usage_unit_cost = models.DecimalField(max_digits=16, decimal_places=6)
    supplier = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-effective_date", "-id"]
        indexes = [
            models.Index(fields=["raw_material", "-effective_date", "-id"], name="rawcost_mat_date_idx"),
            models.Index(fields=["business", "effective_date"], name="rawcost_biz_date_idx"),
        ]

    def __str__(self):
        return f"{self.raw_material.name} — {self.purchase_unit_cost} / {self.raw_material.purchase_unit} — {self.effective_date}"


class SupplierPayment(BusinessOwnedModel):
    date = models.DateField()
    supplier = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=[("Cash", "Cash"), ("Card", "Card"), ("Transfer", "Transfer")], default="Transfer")
    reference = models.CharField(max_length=80, blank=True, default="")
    notes = models.CharField(max_length=255, blank=True, default="")
    purchase_order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    account = models.ForeignKey("core.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_payments")
    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["business", "date"], name="suppay_biz_date_idx"),
        ]
