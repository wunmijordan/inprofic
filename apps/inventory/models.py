from datetime import timedelta
from decimal import Decimal
from django.db import models
from django.utils import timezone
from core.models import BusinessOwnedModel, TimestampedModel


class RawMaterial(BusinessOwnedModel):
    CATEGORY_INGREDIENT = "ingredient"
    CATEGORY_PACKAGING = "packaging"
    CATEGORY_PRODUCTION_SUPPLY = "production_supply"
    CATEGORY_OPERATIONAL_SUPPLY = "operational_supply"

    CATEGORY_CHOICES = [
        (CATEGORY_INGREDIENT, "Ingredient"),
        (CATEGORY_PACKAGING, "Packaging"),
        (CATEGORY_PRODUCTION_SUPPLY, "Production supply (Gas)"),
        (CATEGORY_OPERATIONAL_SUPPLY, "Operational supply (Gloves, Cleaning, etc.)"),
    ]

    name = models.CharField(max_length=120)
    category = models.CharField(
        max_length=24,
        choices=CATEGORY_CHOICES,
        default=CATEGORY_INGREDIENT,
        help_text="Classifies how the material is used. Operational supplies are not tied to a production recipe.",
    )
    purchase_unit = models.CharField(max_length=20, default="", blank=True,
        help_text="What you buy — bag, carton, pack…")
    package_qty = models.DecimalField(max_digits=12, decimal_places=2, default=1,
        help_text="How much is inside ONE purchase unit. E.g. 1 bag = 50 → 50.")
    package_unit = models.CharField(max_length=20, default="", blank=True,
        help_text="The unit package_qty is measured in — kg, g, litre… (what's printed on the pack)")
    usage_unit = models.CharField(max_length=20, default="", blank=True,
        help_text="The fine unit recipes actually consume — kg, g, spoon, cap…")
    usage_conversion_factor = models.DecimalField(max_digits=16, decimal_places=6, default=1,
        help_text="How many usage units in ONE package_unit. Standard: kg→g is 1000, litre→ml is 1000, "
                   "same unit both ways is 1. Non-standard (spoon, cap…): count it yourself, e.g. "
                   "'my spoon holds 5g' → if package_unit is kg, that's 200 spoons per kg → 200.")
    stock = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Stored in usage units. Higher precision prevents tiny per-gram/per-ml
    # costs from being rounded away when procurement prices are converted.
    cost_per_unit = models.DecimalField(max_digits=16, decimal_places=6, default=0)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="unique_raw_material_per_business")]

    def __str__(self):
        return self.name

    @property
    def is_low(self):
        return self.stock is not None and self.reorder_level is not None and self.stock <= self.reorder_level

    @property
    def is_warning(self):
        """Triggers amber warning only for products that have physical-store stock configured."""
        if self.stock is None or self.reorder_level is None or self.is_low:
            return False
        return self.stock <= (self.reorder_level * Decimal("1.5"))

    @property
    def total_conversion_factor(self):
        """Usage units in ONE purchase unit — the number that actually
        matters for receiving stock and costing: package_qty (how much is
        in the pack) x usage_conversion_factor (how that content unit
        breaks down into the fine unit recipes use). E.g. flour: 50 (kg
        per bag) x 1 (kg->kg) = 50. Sugar: 50 (kg per bag) x 1000 (kg->g)
        = 50,000 (g per bag)."""
        return (self.package_qty or Decimal("1")) * (self.usage_conversion_factor or Decimal("1"))

    @property
    def has_unit_conversion(self):
        return bool(self.purchase_unit) and self.total_conversion_factor != 1

    @property
    def stock_breakdown(self):
        """Decomposes current stock (usage unit) into whole purchase units
        + remainder, e.g. 130kg at 50kg/bag -> (2, 30). Only meaningful when
        purchase and usage units actually differ."""
        factor = self.total_conversion_factor
        if factor <= 0:
            return None
        whole = int(self.stock // factor)
        remainder = self.stock - (whole * factor)
        return whole, remainder

    @property
    def reorder_level_purchase_units(self):
        """Stored in usage units, displayed in purchase units."""
        factor = self.total_conversion_factor or Decimal("1")
        return (self.reorder_level / factor).quantize(Decimal("0.01"))

    @property
    def cost_per_purchase_unit(self):
        """cost_per_unit is always stored per (fine) usage unit internally —
        what recipe costing and production deductions run on; this is just
        the purchase-unit-denominated view of the same number for display."""
        return self.cost_per_unit * self.total_conversion_factor


class ProductCategory(BusinessOwnedModel):
    """Business-defined merchandising category for sellable products.

    This is intentionally independent from FinishedGood.source_type so a
    business can keep the operational made-in-house/resale distinction while
    grouping products for storefront discovery (Meals, Drinks, Pastries, etc.).
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="unique_product_category_name_per_business"),
            models.UniqueConstraint(fields=["business", "slug"], name="unique_product_category_slug_per_business"),
        ]

    def __str__(self):
        return self.name


class RawMaterialMeasurementChange(BusinessOwnedModel):
    """Immutable evidence of a material measurement-basis change.

    ``conversion_ratio`` means: one OLD usage unit equals this many NEW usage
    units. Current balances and live operational definitions are converted;
    completed historical movements/cost records remain frozen and this row
    records the interpretation boundary.
    """
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name="measurement_changes")
    conversion_ratio = models.DecimalField(max_digits=20, decimal_places=8, default=1)
    reason = models.CharField(max_length=255)
    old_measurement = models.JSONField(default=dict)
    new_measurement = models.JSONField(default=dict)
    converted_records = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.raw_material.name} measurement change — {self.created_at:%Y-%m-%d}"


class FinishedGood(BusinessOwnedModel):
    SOURCE_MADE_IN_HOUSE = "made_in_house"
    SOURCE_PURCHASED_FOR_RESALE = "purchased_for_resale"
    SOURCE_CHOICES = [
        (SOURCE_MADE_IN_HOUSE, "Made in-house"),
        (SOURCE_PURCHASED_FOR_RESALE, "Purchased for resale"),
    ]

    source_type = models.CharField(
        max_length=24, choices=SOURCE_CHOICES, default=SOURCE_MADE_IN_HOUSE,
        help_text="Whether this sellable product is made by the business or bought from a supplier for resale.",
    )
    name = models.CharField(max_length=120)
    product_category = models.ForeignKey(
        ProductCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="products",
        help_text="Optional business-defined storefront category, e.g. Meals, Drinks, Pastries or Accessories.",
    )
    unit = models.CharField(max_length=20, help_text="loaf, plate, box…")
    units_per_batch = models.DecimalField(max_digits=12, decimal_places=2, default=1,
        help_text="How many individual units one production batch makes, e.g. 41 loaves per batch. "
                   "Recipe quantities are per BATCH, not per unit. Leave at 1 if you don't produce in batches.")
    base_material = models.ForeignKey(
        "RawMaterial", null=True, blank=True, on_delete=models.PROTECT, related_name="base_for_products",
        help_text=(
            "Optional. Choose the main recipe material that can be used to size a production run, "
            "for example rice in a kitchen or flour in a bakery."
        ),
    )
    stock = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, default=0,
        help_text="Optional - Physical Store (shelf) Stock.")
    total_produced = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        help_text="Cumulative all-time production (customer orders + physical store restocks). "
                   "Never decreases — a running total, not current stock.")
    total_delivered_to_customers = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        help_text="Cumulative units delivered via completed customer orders. Only updates when an "
                   "order is completed — same timing as physical store stock, not when merely ordered.")
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, default=0,
        help_text="For Physical Store ONLY.")
    # Default price used when no channel-specific price is configured.
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transferred_market_stock = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text=(
            "Remaining shelf stock that entered through an explicit Market Stock transfer. "
            "This is the only shelf-sale allowance for products not normally configured for Physical Store stock."
        ),
    )

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="unique_finished_good_per_business")]

    def __str__(self):
        return self.name

    @property
    def is_purchased_for_resale(self):
        return self.source_type == self.SOURCE_PURCHASED_FOR_RESALE

    @property
    def is_made_in_house(self):
        return self.source_type == self.SOURCE_MADE_IN_HOUSE

    @property
    def is_low(self):
        """Low-stock status applies only to physical-store products."""
        return (
            self.stock is not None
            and self.reorder_level is not None
            and self.reorder_level > 0
            and self.stock <= self.reorder_level
        )

    @property
    def is_warning(self):
        """Amber warning applies only when a positive physical-store reorder level is configured."""
        if (
            self.stock is None
            or self.reorder_level is None
            or self.reorder_level <= 0
            or self.is_low
        ):
            return False
        return self.stock <= (self.reorder_level * Decimal("1.5"))

    @property
    def is_physical_store_configured(self):
        if self.stock is None:
            return False
        if self.business_id and (not self.business.uses_production or self.is_purchased_for_resale):
            return True
        return self.reorder_level is not None and self.reorder_level > 0

    @property
    def can_sell_from_physical_store(self):
        return self.is_physical_store_configured or self.transferred_market_stock > 0

    @property
    def physical_saleable_stock(self):
        if self.is_physical_store_configured:
            return max(Decimal("0"), Decimal(self.stock or 0))
        return min(
            max(Decimal("0"), Decimal(self.stock or 0)),
            max(Decimal("0"), Decimal(self.transferred_market_stock or 0)),
        )

    def _market_lots(self):
        return list(self.market_stock_lots.all())

    @property
    def market_stock(self):
        today = timezone.localdate()
        return sum(
            (
                Decimal(lot.quantity_available or 0)
                for lot in self._market_lots()
                if lot.active and (lot.expiry_date is None or lot.expiry_date >= today)
            ),
            Decimal("0"),
        )

    @property
    def expired_market_stock(self):
        today = timezone.localdate()
        return sum(
            (
                Decimal(lot.quantity_available or 0)
                for lot in self._market_lots()
                if lot.active and lot.expiry_date is not None and lot.expiry_date < today
            ),
            Decimal("0"),
        )

    @property
    def expiring_market_stock(self):
        deadline = timezone.localdate() + timedelta(days=7)
        today = timezone.localdate()
        return sum(
            (
                Decimal(lot.quantity_available or 0)
                for lot in self._market_lots()
                if lot.active and lot.expiry_date is not None and today <= lot.expiry_date <= deadline
            ),
            Decimal("0"),
        )

    @property
    def uncommitted_planned_offcut_stock(self):
        """Best currently-identifiable planned offcut still sitting in stock.

        Planned offcut enters the same FinishedGood shelf balance as ordinary
        stock, so later POS sales do not identify which source was consumed.
        We therefore report a conservative traceable amount: unreversed
        planned-offcut stock less explicit shortage reconciliations, capped by
        the product's live physical stock.
        """
        # inventory() prefetches these relations; using .all() preserves that
        # cache instead of issuing one query per FinishedGood row.
        batches = [batch for batch in self.production_batches.all() if not batch.is_reversed]
        retained = Decimal("0")
        for batch in batches:
            outgoing = sum((row.quantity for row in batch.reconciliation_out.all()), Decimal("0"))
            retained += max(Decimal("0"), Decimal(batch.planned_surplus_stock_units or 0) - outgoing)
        return min(max(Decimal("0"), Decimal(self.stock or 0)), retained)

    def selling_price_for(self, channel, customer=None):
        """Resolve the selling price in this order:
        customer-specific price -> channel price -> default price.

        Customer overrides are intentionally kept in the Sales app to avoid
        coupling the inventory master price to customer agreements.
        """
        if customer is not None and channel in ("distribution", "online"):
            override = self.customer_prices.filter(customer=customer, channel=channel).first()
            if override is not None:
                return override.price
        configured = self.channel_prices.filter(channel=channel).first()
        return configured.price if configured else self.selling_price

    @property
    def est_cost(self):
        """Estimated ingredient cost PER UNIT (matches selling_price being
        per unit) — recipe quantities are per batch, so this divides the
        batch cost back down by units_per_batch."""
        if self.business_id and (not self.business.uses_production or self.is_purchased_for_resale):
            if hasattr(self, "prefetched_latest_purchase_unit_value"):
                latest_value = self.prefetched_latest_purchase_unit_value
                if latest_value is not None:
                    return latest_value
            else:
                latest_purchase = self.stock_movements.filter(
                    movement_type=StockMovement.FG_PURCHASE,
                    quantity__gt=0,
                ).order_by("-occurred_at", "-id").first()
                if latest_purchase is not None:
                    return latest_purchase.unit_value
        batch_cost = Decimal("0")
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        recipe_items = prefetched.get("recipe_items")
        if recipe_items is None:
            recipe_items = list(self.recipe_items.select_related("raw_material"))
        production_materials = prefetched.get("production_materials")
        if production_materials is None:
            production_materials = list(self.production_materials.select_related("raw_material"))

        for ri in recipe_items:
            batch_cost += ri.raw_material.cost_per_unit * ri.qty_per_batch
        for pm in production_materials:
            batch_cost += pm.raw_material.cost_per_unit * pm.qty_per_batch
        if not recipe_items and not production_materials:
            if hasattr(self, "prefetched_latest_purchase_unit_value"):
                latest_value = self.prefetched_latest_purchase_unit_value
                if latest_value is not None:
                    return latest_value
            else:
                latest_purchase = self.stock_movements.filter(
                    movement_type=StockMovement.FG_PURCHASE,
                    quantity__gt=0,
                ).order_by("-occurred_at", "-id").first()
                if latest_purchase is not None:
                    return latest_purchase.unit_value
        upb = self.units_per_batch or Decimal("1")
        return batch_cost / upb


class FinishedGoodChannelPrice(TimestampedModel):
    CHANNEL_PHYSICAL_STORE = "physical_store"
    CHANNEL_DISTRIBUTION = "distribution"
    CHANNEL_ONLINE = "online"

    CHANNEL_CHOICES = [
        (CHANNEL_PHYSICAL_STORE, "Physical Store"),
        (CHANNEL_DISTRIBUTION, "Distribution"),
        (CHANNEL_ONLINE, "Online"),
    ]

    finished_good = models.ForeignKey(
        FinishedGood, related_name="channel_prices", on_delete=models.CASCADE
    )
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        unique_together = ("finished_good", "channel")
        ordering = ["channel"]

    def __str__(self):
        return f"{self.finished_good.name} — {self.get_channel_display()} — {self.price}"


class RecipeItem(TimestampedModel):
    """Line item, not independently business-owned — scoped implicitly
    through finished_good. Quantities are PER BATCH of the finished good,
    not per individual unit — this matches how production actually happens
    (a whole batch of dough at once), not how it's shelved/sold."""
    finished_good = models.ForeignKey(FinishedGood, related_name="recipe_items", on_delete=models.CASCADE)
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.CASCADE)
    qty_per_batch = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    flexible_usage = models.BooleanField(
        default=False,
        help_text="Allow the quantity per batch to be adjusted when the production order is approved (useful for yeast or other urgency-sensitive ingredients).",
    )

    class Meta:
        unique_together = ("finished_good", "raw_material")

    def __str__(self):
        return f"{self.qty_per_batch} {self.raw_material.usage_unit} {self.raw_material.name} / batch of {self.finished_good.name}"


class ProductionMaterial(TimestampedModel):
    """A raw material that is consumed as part of producing a finished good,
    but is not necessarily a recipe ingredient. Examples: cake boxes,
    packaging sleeves, baking gas and other per-batch production supplies.

    Quantities are per BATCH and are always entered in the raw material's
    usage unit, just like RecipeItem.
    """
    finished_good = models.ForeignKey(
        FinishedGood,
        related_name="production_materials",
        on_delete=models.CASCADE,
    )
    raw_material = models.ForeignKey(
        RawMaterial,
        related_name="production_material_links",
        on_delete=models.PROTECT,
    )
    qty_per_batch = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = ("finished_good", "raw_material")
        ordering = ["raw_material__name"]

    def __str__(self):
        return f"{self.qty_per_batch} {self.raw_material.usage_unit} {self.raw_material.name} / batch of {self.finished_good.name}"


class InventoryLocation(BusinessOwnedModel):
    name = models.CharField(max_length=80)
    location_type = models.CharField(max_length=30, default="store")
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="unique_inventory_location_per_business")]
        ordering = ["name"]
    def __str__(self): return self.name


class StockAdjustment(BusinessOwnedModel):
    REASON_CHOICES = [
        ("count", "Physical count correction"), ("wastage", "Wastage / spoilage"),
        ("damage", "Damage"), ("return_customer", "Customer return"),
        ("return_supplier", "Supplier return"), ("internal", "Business use"),
        ("charity", "Charity / donation"), ("staff", "Staff issue"), ("other", "Other"),
    ]
    date = models.DateField()
    raw_material = models.ForeignKey(RawMaterial, null=True, blank=True, on_delete=models.PROTECT, related_name="adjustments")
    finished_good = models.ForeignKey(FinishedGood, null=True, blank=True, on_delete=models.PROTECT, related_name="adjustments")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default="count")
    description = models.CharField(max_length=255)
    unit_value = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    location = models.ForeignKey(InventoryLocation, null=True, blank=True, on_delete=models.PROTECT)
    reversed = models.BooleanField(default=False)
    class Meta: ordering = ["-date", "-id"]
    @property
    def value(self): return self.quantity * self.unit_value


class OperationalSupplyDispense(BusinessOwnedModel):
    """Records operational supplies consumed outside production recipes.

    Quantities are entered in the material's usage unit and create a stock
    movement, so operational supplies participate in the same inventory
    ledger and reorder monitoring as production materials.
    """
    REASON_CHOICES = [
        ("cleaning", "Cleaning / sanitation"),
        ("staff", "Staff use"),
        ("office", "Office / administrative use"),
        ("maintenance", "Maintenance"),
        ("charity", "Charity / service"),
        ("other", "Other"),
    ]
    date = models.DateField()
    raw_material = models.ForeignKey(
        RawMaterial, on_delete=models.PROTECT, related_name="operational_dispenses"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default="other")
    description = models.CharField(max_length=255)
    location = models.ForeignKey(
        "InventoryLocation", null=True, blank=True, on_delete=models.PROTECT, related_name="operational_dispenses"
    )

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.raw_material.name} — {self.quantity} {self.raw_material.usage_unit}"


class MarketStockLot(BusinessOwnedModel):
    """A batch-aware pool reserved exclusively for future Distribution sales."""

    SOURCE_PRODUCTION = "production"
    SOURCE_RETURN = "return"
    SOURCE_CHOICES = [
        (SOURCE_PRODUCTION, "Market-stock production"),
        (SOURCE_RETURN, "Redistributable customer return"),
    ]

    finished_good = models.ForeignKey(
        FinishedGood, on_delete=models.PROTECT, related_name="market_stock_lots"
    )
    production_batch = models.ForeignKey(
        "production.ProductionBatch", null=True, blank=True, on_delete=models.PROTECT,
        related_name="market_stock_lots",
    )
    source_sale_item = models.ForeignKey(
        "sales.SaleItem", null=True, blank=True, on_delete=models.PROTECT,
        related_name="returned_market_lots",
    )
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES)
    received_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    quantity_received = models.DecimalField(max_digits=14, decimal_places=2)
    quantity_available = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    active = models.BooleanField(default=True)
    closed_reason = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        ordering = ["expiry_date", "received_date", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_received__gte=0),
                name="market_lot_received_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_available__gte=0),
                name="market_lot_available_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_available__lte=models.F("quantity_received")),
                name="market_lot_available_not_above_received",
            ),
            models.UniqueConstraint(
                fields=["production_batch"],
                condition=models.Q(source="production"),
                name="one_market_production_lot_per_batch",
            ),
        ]

    def __str__(self):
        return f"{self.finished_good.name} — {self.quantity_available} available"

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    @property
    def is_expiring_soon(self):
        if not self.expiry_date or self.is_expired:
            return False
        return self.expiry_date <= timezone.localdate() + timedelta(days=7)

    @property
    def available_value(self):
        return self.quantity_available * self.unit_cost


class MarketStockMovement(BusinessOwnedModel):
    PRODUCTION_IN = "production_in"
    RELEASE = "release"
    RETURN_IN = "return_in"
    TRANSFER_PHYSICAL = "transfer_physical"
    EXPIRY = "expiry"
    REVERSAL = "reversal"
    TYPE_CHOICES = [
        (PRODUCTION_IN, "Production received into Market Stock"),
        (RELEASE, "Released to distributor"),
        (RETURN_IN, "Redistributable return received"),
        (TRANSFER_PHYSICAL, "Transferred to Physical Store"),
        (EXPIRY, "Expired / unsellable write-off"),
        (REVERSAL, "Production reversal"),
    ]

    lot = models.ForeignKey(MarketStockLot, on_delete=models.PROTECT, related_name="movements")
    date = models.DateField()
    movement_type = models.CharField(max_length=24, choices=TYPE_CHOICES)
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    customer = models.ForeignKey(
        "sales.Customer", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="market_stock_movements",
    )
    sale = models.ForeignKey(
        "sales.Sale", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="market_stock_movements",
    )
    unit_value = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-date", "-id"]

    @property
    def value(self):
        return self.quantity * self.unit_value

    def __str__(self):
        return f"{self.date} — {self.get_movement_type_display()} — {self.quantity}"


class DistributionReturn(BusinessOwnedModel):
    REDISTRIBUTABLE = "redistributable"
    DAMAGED = "damaged"
    CONDITION_CHOICES = [
        (REDISTRIBUTABLE, "Unsold and suitable for redistribution"),
        (DAMAGED, "Damaged / unsellable"),
    ]

    sale_item = models.ForeignKey(
        "sales.SaleItem", on_delete=models.PROTECT, related_name="distribution_returns"
    )
    date = models.DateField()
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES)
    reason = models.CharField(max_length=255)
    unit_value = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    market_lot = models.OneToOneField(
        MarketStockLot, null=True, blank=True, on_delete=models.PROTECT,
        related_name="distribution_return",
    )

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="distribution_return_quantity_positive",
            )
        ]

    @property
    def writeoff_value(self):
        if self.condition != self.DAMAGED:
            return Decimal("0")
        return self.quantity * self.unit_value

    def __str__(self):
        return f"Sale #{self.sale_item.sale_id} return — {self.quantity}"


class StockMovement(BusinessOwnedModel):
    RAW_PURCHASE = "raw_purchase"
    RAW_CONSUMPTION = "raw_consumption"
    FG_PRODUCTION = "fg_production"
    FG_PURCHASE = "fg_purchase"
    FG_SALE = "fg_sale"
    FG_UNPAID_ISSUE = "fg_unpaid_issue"
    FG_WASTAGE = "fg_wastage"
    FG_MARKET_PRODUCTION = "fg_market_production"
    FG_MARKET_RELEASE = "fg_market_release"
    FG_MARKET_RETURN = "fg_market_return"
    FG_MARKET_TRANSFER = "fg_market_transfer"
    FG_MARKET_EXPIRY = "fg_market_expiry"
    FG_DISTRIBUTION_DAMAGE = "fg_distribution_damage"
    OPERATIONAL_DISPENSE = "operational_dispense"
    ADJUSTMENT = "adjustment"

    MOVEMENT_TYPES = [
        (RAW_PURCHASE, "Raw material purchase"),
        (RAW_CONSUMPTION, "Raw material consumption"),
        (FG_PRODUCTION, "Finished goods production"),
        (FG_PURCHASE, "Stock product purchase"),
        (FG_SALE, "Finished goods sale"),
        (FG_UNPAID_ISSUE, "Unpaid product issue"),
        (FG_WASTAGE, "Production wastage"),
        (FG_MARKET_PRODUCTION, "Production received into Distribution Market Stock"),
        (FG_MARKET_RELEASE, "Market Stock released to distributor"),
        (FG_MARKET_RETURN, "Redistributable Distribution return"),
        (FG_MARKET_TRANSFER, "Market Stock transferred to Physical Store"),
        (FG_MARKET_EXPIRY, "Expired Market Stock write-off"),
        (FG_DISTRIBUTION_DAMAGE, "Damaged Distribution return"),
        (OPERATIONAL_DISPENSE, "Operational supply dispense"),
        (ADJUSTMENT, "Adjustment"),
    ]

    raw_material = models.ForeignKey(
        RawMaterial,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="stock_movements",
    )

    finished_good = models.ForeignKey(
        FinishedGood,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="stock_movements",
    )

    movement_type = models.CharField(
        max_length=30,
        choices=MOVEMENT_TYPES,
    )

    quantity = models.DecimalField(
        max_digits=15,
        decimal_places=3,
        help_text="Signed quantity in the item's stock tracking unit.",
    )

    # A finished good can be produced for physical store stock or directly
    # for a customer order. Customer-order production contributes to
    # total_produced but does not change physical shelf stock.
    affects_stock = models.BooleanField(
        default=True,
        help_text="Whether this movement changes the item's physical stock balance.",
    )

    balance_after = models.DecimalField(
        max_digits=15,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Stock balance immediately after this movement. Null for non-stock events.",
    )

    occurred_at = models.DateTimeField(auto_now_add=True)

    note = models.CharField(max_length=255, blank=True, default="")
    reference = models.CharField(max_length=80, blank=True, default="")
    unit_value = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    location = models.ForeignKey(InventoryLocation, null=True, blank=True, on_delete=models.PROTECT, related_name="stock_movements")

    class Meta:
        ordering = ["-occurred_at"]
