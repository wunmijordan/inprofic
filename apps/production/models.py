from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from core.models import BusinessOwnedModel, TimestampedModel


class Order(BusinessOwnedModel):
    """A production order — either for a specific customer (made to order,
    delivered directly, never touches shelf stock) or a physical store
    restock (adds to shelf stock on completion, not tied to any customer).
    Approved and completed as a whole — all its line items together, not
    item-by-item. See docs/ARCHITECTURE.md for the full flow."""

    TYPE_CHOICES = [("distribution", "Distribution Order"), ("online", "Online Order"), ("physical_store", "Physical Store Order")]
    STATUS_CHOICES = [
        ("pending", "Pending"), ("approved", "Approved"),
        ("completed", "Completed"), ("rejected", "Rejected"), ("reversed", "Reversed"),
    ]
    PAYMENT_CHOICES = [("Cash", "Cash"), ("Card", "Card"), ("Transfer", "Transfer")]
    TRANSACTION_CHOICES = [("paid", "Paid"), ("unpaid", "Unpaid")]
    DESTINATION_CHOICES = [
        ("store", "Store replenishment — add to Physical Store stock"),
        ("non_stock", "Non-stock purpose — do not add to Physical Store stock"),
    ]
    CUSTOMER_PAYMENT_CHOICES = [("paid", "Received"), ("unpaid", "Receivable")]

    date = models.DateField()
    order_number = models.PositiveBigIntegerField(editable=False)
    order_type = models.CharField(max_length=15, choices=TYPE_CHOICES, default="physical_store")
    is_market_stock = models.BooleanField(
        default=False,
        help_text=(
            "Distribution orders only: produce without assigning a customer and retain "
            "the completed goods as available stock for future sales."
        ),
    )
    production_destination = models.CharField(
        max_length=12, choices=DESTINATION_CHOICES, default="store",
        help_text="Physical Store orders only: choose whether completed production enters Shelf Stock or is for a non-stock purpose.",
    )
    non_stock_purpose = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Specific purpose when production is not going to Physical Store stock (e.g. Staff Welfare, Gift, Charity).",
    )
    customer = models.ForeignKey("sales.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="production_orders")
    customer_name = models.CharField(max_length=120, blank=True,
        help_text="Required for distribution and online orders; leave blank for a physical store restock.")
    customer_region = models.CharField(max_length=100, blank=True,
        help_text="Optional reporting region/territory for distribution or online customer analytics.")
    customer_group = models.CharField(max_length=100, blank=True,
        help_text="Optional customer group/segment for distribution or online customer analytics.")
    # Existing payment fields are retained for historical rows and database
    # compatibility. They are deliberately not exposed for Physical Store
    # Orders: those are production/restock requests, not direct sales.
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_CHOICES, default="paid", help_text="Earlier physical-store order payment field; direct sales are recorded in Sales.")
    customer_payment_status = models.CharField(max_length=10, choices=CUSTOMER_PAYMENT_CHOICES, default="paid", help_text="Distribution/Online only: whether the customer payment has been received or remains a receivable.")
    customer_payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default="Transfer", blank=True)
    customer_payment_account = models.ForeignKey("core.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="customer_order_payments")
    unpaid_description = models.CharField(max_length=255, blank=True, default="")
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default="Cash", blank=True,
        help_text="Earlier customer-order payment field; current customer payments are handled through Finance.")
    account = models.ForeignKey("core.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="production_orders")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    notes = models.TextField(blank=True)
    approved_date = models.DateField(null=True, blank=True)
    completed_date = models.DateField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_reason = models.CharField(max_length=255, blank=True, default="")
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reversed_production_orders")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "order_number"], name="unique_order_number_per_business"),
        ]

    def save(self, *args, **kwargs):
        if self.order_number is None:
            if not self.business_id:
                raise ValueError("Order business must be set before allocating a business order number.")
            with transaction.atomic():
                sequence, _ = OrderNumberSequence.raw_objects.select_for_update().get_or_create(
                    business_id=self.business_id,
                    defaults={"next_number": 1, "created_by_id": self.created_by_id},
                )
                self.order_number = sequence.next_number
                sequence.next_number += 1
                sequence.save(update_fields=["next_number", "updated_at"])
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    @property
    def display_number(self):
        return self.order_number or self.pk

    @property
    def display_order_type(self):
        from core.verticals import vertical_config
        choices = dict(vertical_config(self.business)["order_types"])
        return choices.get(self.order_type, self.get_order_type_display())

    @property
    def is_market_stock_order(self):
        """True only for explicitly unassigned Distribution production.

        The stored flag defaults to False so pre-existing customer orders keep
        their historical completion, sale and payment behaviour.
        """
        return self.order_type == "distribution" and self.is_market_stock

    @property
    def is_customer_order(self):
        return self.order_type in ("distribution", "online") and not self.is_market_stock_order

    def __str__(self):
        if self.is_market_stock_order:
            who = "Market stock"
        elif self.order_type in ("distribution", "online"):
            who = self.customer_name
        else:
            who = "Physical store"
        return f"Order #{self.display_number} — {who}"

    @property
    def total(self):
        return sum((i.line_total for i in self.items.all()), Decimal("0"))

    @property
    def total_units(self):
        return sum((i.total_units for i in self.items.all()), Decimal("0"))

    def material_requirements(self):
        """Raw material needed across every line item.

        Both recipe ingredients and per-production inputs (packaging, gas,
        production supplies) are included. Quantities are exact batch+piece
        requirements and are returned in each material's usage unit.
        """
        needed = {}
        for item in self.items.select_related("finished_good"):
            good = item.finished_good
            upb = good.units_per_batch or Decimal("1")
            production_batches = item.effective_production_batch_qty
            production_pieces = item.effective_production_piece_qty
            per_piece_factor = production_pieces / upb

            links = list(good.recipe_items.select_related("raw_material"))
            links += list(good.production_materials.select_related("raw_material"))

            for link in links:
                qty = link.qty_per_batch * production_batches
                qty += link.qty_per_batch * per_piece_factor
                mat = link.raw_material
                current = needed.get(mat.id)
                needed[mat.id] = (mat, (current[1] if current else Decimal("0")) + qty)
        return needed

    def shortages(self):
        result = []
        for mat, needed in self.material_requirements().values():
            if needed > mat.stock:
                result.append({
                    "name": mat.name,
                    "category": mat.get_category_display(),
                    "usage_unit": mat.usage_unit,
                    "needed": needed,
                    "have": mat.stock,
                    "short": needed - mat.stock,
                })
        return result


class OrderNumberSequence(BusinessOwnedModel):
    """Per-business visible production-order sequence.

    The database primary key remains global and is never reset for tenant
    numbering. `next_number` controls only the human-facing Order # within a
    business, which makes the sequence safe for multi-tenant operation.
    """
    next_number = models.PositiveBigIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business"], name="unique_order_number_sequence_per_business"),
        ]

    def __str__(self):
        return f"{self.business} — next Order #{self.next_number}"


class OrderItem(TimestampedModel):
    BASIS_PRODUCT_QUANTITY = "product_quantity"
    BASIS_BASE_MATERIAL = "base_material"
    PRODUCTION_BASIS_CHOICES = [
        (BASIS_PRODUCT_QUANTITY, "Product quantity"),
        (BASIS_BASE_MATERIAL, "Base material"),
    ]

    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    finished_good = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT)
    production_basis = models.CharField(
        max_length=24, choices=PRODUCTION_BASIS_CHOICES, default=BASIS_PRODUCT_QUANTITY,
        help_text="Choose whether this production run is sized by product quantity or by the product's base material.",
    )
    base_material_quantity = models.DecimalField(
        max_digits=14, decimal_places=4, default=0, blank=True,
        help_text="Quantity of the product's selected base material to use for this production run.",
    )
    batch_qty = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    piece_qty = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    production_batch_qty = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, blank=True,
        help_text="Optional production plan. Leave 0/blank to produce exactly the ordered quantity.",
    )
    production_piece_qty = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, blank=True,
        help_text="Optional loose pieces in the production plan. Leave 0/blank to produce exactly the ordered quantity.",
    )
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0,
        help_text="Snapshot of the product's selling price at order time — set automatically.")

    def __str__(self):
        return f"{self.finished_good.name} — {self.total_units} units"

    @property
    def base_material_batch_factor(self):
        if self.production_basis != self.BASIS_BASE_MATERIAL or not self.finished_good_id:
            return None
        if not self.finished_good.base_material_id:
            return None
        recipe_row = self.finished_good.recipe_items.filter(
            raw_material_id=self.finished_good.base_material_id
        ).first()
        if recipe_row is None or recipe_row.qty_per_batch <= 0:
            return None
        return (self.base_material_quantity or Decimal("0")) / recipe_row.qty_per_batch

    @property
    def total_units(self):
        upb = self.finished_good.units_per_batch or Decimal("1")
        factor = self.base_material_batch_factor
        if factor is not None:
            return factor * upb
        return self.batch_qty * upb + self.piece_qty

    @property
    def has_explicit_production_plan(self):
        return (self.production_batch_qty or Decimal("0")) > 0 or (self.production_piece_qty or Decimal("0")) > 0

    @property
    def effective_production_batch_qty(self):
        if self.has_explicit_production_plan:
            return self.production_batch_qty
        factor = self.base_material_batch_factor
        return factor if factor is not None else self.batch_qty

    @property
    def effective_production_piece_qty(self):
        return self.production_piece_qty if self.has_explicit_production_plan else self.piece_qty

    @property
    def production_total_units(self):
        upb = self.finished_good.units_per_batch or Decimal("1")
        return self.effective_production_batch_qty * upb + self.effective_production_piece_qty

    @property
    def planned_overproduction_units(self):
        return max(Decimal("0"), self.production_total_units - self.total_units)

    @property
    def line_total(self):
        """Discount is applied PER UNIT, not once on the line total — e.g.
        50 units at 1500 with a 200 discount is (1500-200)*50 = 65,000,
        not 1500*50-200. Matches how a per-item price cut actually works."""
        return self.total_units * ((self.price or Decimal("0")) - (self.discount or Decimal("0")))
class OrderMaterialUsage(BusinessOwnedModel):
    """Actual raw-material quantity released for one production order item.

    Created at approval so flexible recipe ingredients can vary from the base
    recipe while preserving the quantity used for stock release and costing.
    """
    order = models.ForeignKey(Order, related_name="material_usages", on_delete=models.CASCADE)
    order_item = models.ForeignKey(OrderItem, related_name="material_usages", on_delete=models.CASCADE)
    raw_material = models.ForeignKey("inventory.RawMaterial", on_delete=models.PROTECT)
    planned_quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    actual_quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    flexible = models.BooleanField(default=False)

    class Meta:
        ordering = ["order_item_id", "raw_material__name"]
        constraints = [
            models.UniqueConstraint(fields=["order_item", "raw_material"], name="unique_material_usage_per_order_item")
        ]

    def __str__(self):
        return f"Order #{self.order.display_number} — {self.raw_material.name}: {self.actual_quantity}"


class ProductionRun(BusinessOwnedModel):
    """Optional parent production event spanning several customer/store orders.

    A shared run coordinates several commercial Orders as one production
    exercise. Each member Order keeps its own customer/channel/pricing and its
    normal proportional recipe requirement. Approval aggregates those exact
    requirements and releases the combined quantities together.
    """
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("approved", "Approved / in production"),
        ("completed", "Completed"),
    ]

    date = models.DateField()
    run_number = models.CharField(max_length=60)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="draft")
    notes = models.TextField(blank=True, default="")
    approved_date = models.DateField(null=True, blank=True)
    completed_date = models.DateField(null=True, blank=True)
    orders = models.ManyToManyField(Order, through="ProductionRunOrder", related_name="production_runs")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "run_number"], name="unique_production_run_number_per_business")
        ]

    def __str__(self):
        return self.run_number


class ProductionRunOrder(TimestampedModel):
    production_run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="order_links")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="production_run_links")

    class Meta:
        ordering = ["order_id"]
        constraints = [
            models.UniqueConstraint(fields=["order"], name="order_in_at_most_one_production_run")
        ]

    def __str__(self):
        return f"{self.production_run.run_number} — Order #{self.order.display_number}"


class ProductionRunMaterial(BusinessOwnedModel):
    """Shared-material override rows retained for existing records.

    New Shared Production Runs do not use run-level material substitution.
    They aggregate each OrderItem's normal proportional recipe/input usage.
    This model remains only so existing databases/history created by the older
    shared-run design remain readable without destructive schema changes.
    """
    production_run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="shared_materials")
    raw_material = models.ForeignKey("inventory.RawMaterial", on_delete=models.PROTECT, related_name="shared_production_runs")
    planned_quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    actual_quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    class Meta:
        ordering = ["raw_material__name"]
        constraints = [
            models.UniqueConstraint(fields=["production_run", "raw_material"], name="unique_shared_material_per_production_run")
        ]

    def __str__(self):
        return f"{self.production_run.run_number} — {self.raw_material.name}: {self.actual_quantity}"


class ProductionBatch(BusinessOwnedModel):
    """A traceable production output record for one finished-good order line.

    The batch records planned output, gross production, wastage and saleable
    output. It is the bridge between the production event, its frozen cost,
    quality inspection and any customer/shelf movement that follows.
    """
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="production_batches")
    production_run = models.ForeignKey("ProductionRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="production_batches")
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="production_batches")
    finished_good = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT, related_name="production_batches")
    production_date = models.DateField()
    batch_number = models.CharField(max_length=60)
    expiry_date = models.DateField(null=True, blank=True)
    planned_units = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    ordered_units = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Customer/requested quantity kept separately from the production target.",
    )
    planned_surplus_stock_units = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Saleable planned overproduction retained as general Physical Store stock.",
    )
    planned_surplus_customer_units = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    planned_surplus_customer = models.ForeignKey("sales.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="planned_offcut_batches")
    planned_surplus_customer_channel = models.CharField(max_length=20, blank=True, default="")
    planned_surplus_sale = models.ForeignKey("sales.Sale", null=True, blank=True, on_delete=models.SET_NULL, related_name="planned_offcut_batches")
    is_reversed = models.BooleanField(default=False)
    produced_units = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Gross units produced before wastage/rejection.")
    wastage_units = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Units lost, rejected or otherwise not saleable.")
    wastage_reason = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    total_cost = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=6, default=0)

    # A customer order is fulfilled at the ordered quantity. If gross
    # production is sufficient but wastage/rejection leaves fewer saleable
    # units, the deficit is recorded explicitly rather than pretending the
    # order was produced short. Reconciliation is a separate auditable
    # allocation from available surplus production of the same product, regardless of channel.
    shortage_flag = models.BooleanField(default=False)
    shortage_reason = models.CharField(max_length=255, blank=True, default="")

    # Saleable output above planned units must be explicitly accounted for.
    # Excess assigned to shelf stock remains available for later shortage
    # reconciliation; excess assigned to a non-stock purpose is consumed by
    # that purpose and is not available to satisfy another order.
    excess_stock_units = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    excess_market_stock_units = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Additional saleable output from an unassigned Distribution order retained in Market Stock.",
    )
    excess_non_stock_units = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    excess_non_stock_purpose = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-production_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["business", "batch_number"], name="unique_production_batch_per_business")]

    def __str__(self):
        return f"{self.batch_number} — {self.finished_good.name}"

    @property
    def saleable_units(self):
        return max(Decimal("0"), self.produced_units - self.wastage_units)

    @property
    def yield_percent(self):
        if not self.planned_units:
            return Decimal("0")
        return (self.saleable_units / self.planned_units * Decimal("100")).quantize(Decimal("0.01"))

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    @property
    def is_expiring_soon(self):
        if not self.expiry_date or self.is_expired:
            return False
        return self.expiry_date <= timezone.localdate() + timedelta(days=7)

    @property
    def shortage_units(self):
        required = self.ordered_units or self.planned_units
        return max(Decimal("0"), required - self.saleable_units)

    @property
    def reconciled_units(self):
        return sum((r.quantity for r in self.reconciliation_in.all()), Decimal("0"))

    @property
    def outstanding_shortage_units(self):
        return max(Decimal("0"), self.shortage_units - self.reconciled_units)

    @property
    def excess_units(self):
        """Unplanned saleable output above the explicit production target."""
        return max(Decimal("0"), self.saleable_units - self.planned_units)

    @property
    def total_surplus_units(self):
        """All saleable output above the customer/requested quantity."""
        required = self.ordered_units or self.planned_units
        return max(Decimal("0"), self.saleable_units - required)

    @property
    def available_surplus_units(self):
        """Excess units deliberately retained in shelf stock and still
        available to satisfy a shortage from any production channel."""
        outgoing = sum((r.quantity for r in self.reconciliation_out.all()), Decimal("0"))
        retained = max(Decimal("0"), self.planned_surplus_stock_units + self.excess_stock_units - outgoing)
        physical_stock = max(Decimal("0"), Decimal(self.finished_good.stock or 0))
        return min(retained, physical_stock)


class ProductionOffcutAllocation(BusinessOwnedModel):
    """One customer allocation from a production batch's planned offcut.

    New completions may split planned offcut across any number of Distribution
    and Online customers.  The existing single-customer fields on ProductionBatch
    remain as summary/backward-compatibility fields for older records.
    """
    batch = models.ForeignKey(
        ProductionBatch, on_delete=models.CASCADE, related_name="offcut_allocations"
    )
    customer = models.ForeignKey(
        "sales.Customer", on_delete=models.PROTECT, related_name="planned_offcut_allocations"
    )
    channel = models.CharField(
        max_length=20, choices=[("distribution", "Distribution"), ("online", "Online")]
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    sale = models.ForeignKey(
        "sales.Sale", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="planned_offcut_allocations"
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.batch.batch_number} — {self.customer.name}: {self.quantity}"


class ProductionBatchReconciliation(BusinessOwnedModel):
    """Auditable fulfillment allocation from surplus production to a
    customer-order production shortage. It is not a new sale, cash entry, or production event.
    When the source surplus sits in shelf stock, reconciliation removes the
    allocated quantity from physical stock."""

    source_batch = models.ForeignKey(
        "ProductionBatch",
        on_delete=models.PROTECT,
        related_name="reconciliation_out",
    )
    target_batch = models.ForeignKey(
        "ProductionBatch",
        on_delete=models.PROTECT,
        related_name="reconciliation_in",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=255)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.quantity} — {self.source_batch.batch_number} → {self.target_batch.batch_number}"


class ProductionQualityCheck(BusinessOwnedModel):
    """Quality inspection attached to a completed production batch."""
    STATUS_CHOICES = [
        ("pending", "Pending inspection"),
        ("passed", "Passed"),
        ("conditional", "Passed with conditions"),
        ("failed", "Failed"),
    ]
    batch = models.OneToOneField(ProductionBatch, on_delete=models.CASCADE, related_name="quality_check")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="pending")
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="production_quality_checks")
    checked_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    defects = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-checked_at", "-id"]

    def __str__(self):
        return f"QC — {self.batch.batch_number} — {self.get_status_display()}"


class ProductionCostSnapshot(BusinessOwnedModel):
    """Frozen production cost calculated when an order is completed.

    Each raw-material line uses the latest received procurement cost available
    on the production date. Older procurement prices are never averaged in.
    """
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="cost_snapshots")
    order_item = models.ForeignKey(OrderItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="cost_snapshots")
    production_batch = models.ForeignKey("ProductionBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="cost_snapshots")
    finished_good = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT, related_name="production_cost_snapshots")
    production_date = models.DateField()
    produced_units = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    cost_source = models.CharField(max_length=40, default="latest_procurement")
    batch_number = models.CharField(max_length=40, blank=True, default="")
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-production_date", "-id"]

    def __str__(self):
        return f"{self.finished_good.name} — {self.unit_cost} / unit — {self.production_date}"


class ProductionCostLine(TimestampedModel):
    snapshot = models.ForeignKey(ProductionCostSnapshot, related_name="lines", on_delete=models.CASCADE)
    raw_material = models.ForeignKey("inventory.RawMaterial", on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    usage_unit_cost = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    total_cost = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    source = models.CharField(max_length=20, default="latest_procurement")

    def __str__(self):
        return f"{self.snapshot.finished_good.name} — {self.raw_material.name}"
