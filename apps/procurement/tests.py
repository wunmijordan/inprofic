from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from core.models import Business
from core.verticals import vertical_config
from inventory.models import FinishedGood, StockMovement
from sales.models import Customer, CustomerProductPrice, Sale

from .forms import PurchaseOrderItemForm
from .models import PurchaseOrder, PurchaseOrderItem


class DirectProductProcurementTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Trade Hub",
            slug="trade-hub",
            vertical=Business.VERTICAL_WHOLESALE,
        )
        self.user = CustomUser.objects.create_superuser(
            username="trade-admin",
            password="safe-password-123",
            fullname="Trade Admin",
        )
        self.product = FinishedGood.raw_objects.create(
            business=self.business,
            created_by=self.user,
            name="Cooking Oil",
            unit="carton",
            stock=Decimal("0"),
            reorder_level=Decimal("0"),
            selling_price=Decimal("75"),
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()

    def receive_product(self, quantity="12", unit_cost="50"):
        po = PurchaseOrder.raw_objects.create(
            business=self.business,
            created_by=self.user,
            date=date(2026, 9, 4),
            supplier="Main Distributor",
            payment_status="unpaid",
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            finished_good=self.product,
            qty=Decimal(quantity),
            unit_cost=Decimal(unit_cost),
        )
        response = self.client.post(reverse("po_receive", args=[po.pk]))
        self.assertRedirects(response, reverse("procurement_list"), fetch_redirect_response=False)
        return po

    def test_receiving_product_records_arrival_stock_and_cost(self):
        po = self.receive_product()

        po.refresh_from_db()
        self.product.refresh_from_db()
        movement = StockMovement.raw_objects.get(
            business=self.business,
            finished_good=self.product,
            movement_type=StockMovement.FG_PURCHASE,
        )
        self.assertEqual(po.status, "received")
        self.assertEqual(self.product.stock, Decimal("12.00"))
        self.assertEqual(movement.quantity, Decimal("12.000"))
        self.assertEqual(movement.balance_after, Decimal("12.000"))
        self.assertEqual(movement.unit_value, Decimal("50"))
        self.assertEqual(movement.reference, f"PO-{po.pk}")
        self.assertEqual(self.product.est_cost, Decimal("50"))

        # A repeated receive request must not duplicate live stock effects.
        self.client.post(reverse("po_receive", args=[po.pk]))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("12.00"))
        self.assertEqual(
            StockMovement.raw_objects.filter(
                business=self.business,
                finished_good=self.product,
                movement_type=StockMovement.FG_PURCHASE,
            ).count(),
            1,
        )

    def test_purchase_order_form_creates_a_direct_product_line(self):
        response = self.client.post(reverse("po_add"), {
            "date": "2026-09-04",
            "supplier": "Main Distributor",
            "payment_status": "unpaid",
            "payment_method": "Transfer",
            "account": "",
            "amount_paid": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-item": f"finished:{self.product.pk}",
            "items-0-qty": "8",
            "items-0-unit_cost": "410.00",
        })

        self.assertRedirects(response, reverse("procurement_list"), fetch_redirect_response=False)
        line = PurchaseOrderItem.objects.get()
        self.assertIsNone(line.raw_material)
        self.assertEqual(line.finished_good, self.product)
        self.assertEqual(line.qty, Decimal("8"))
        self.assertEqual(line.unit_cost, Decimal("51.25"))

    def test_purchase_form_normalizes_fractional_quantity_total_cost(self):
        material = RawMaterial.raw_objects.create(
            business=self.business,
            name="Sugar",
            purchase_unit="bag",
            package_qty=Decimal("50"),
            package_unit="kg",
            usage_unit="kg",
            usage_conversion_factor=Decimal("1"),
            cost_per_unit=Decimal("1320"),
        )
        response = self.client.post(reverse("po_add"), {
            "date": "2026-09-14",
            "supplier": "Sugar Supplier",
            "payment_status": "unpaid",
            "payment_method": "Transfer",
            "account": "",
            "amount_paid": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-item": f"raw:{material.pk}",
            "items-0-qty": "0.50",
            # Staff enters what was actually paid for the half bag.
            "items-0-unit_cost": "35000.00",
        })

        self.assertRedirects(response, reverse("procurement_list"), fetch_redirect_response=False)
        line = PurchaseOrderItem.objects.get(raw_material=material)
        self.assertEqual(line.qty, Decimal("0.50"))
        self.assertEqual(line.unit_cost, Decimal("70000.00"))
        self.assertEqual(line.line_total, Decimal("35000.0000"))


    def test_purchase_item_choices_exclude_another_business(self):
        other = Business.objects.create(name="Other", slug="other", vertical=Business.VERTICAL_RETAIL)
        foreign_product = FinishedGood.raw_objects.create(
            business=other,
            name="Foreign Product",
            unit="piece",
            stock=Decimal("1"),
        )

        form = PurchaseOrderItemForm(business=self.business)
        choice_values = {
            value
            for _group, choices in form.fields["item"].choices[1:]
            for value, _label in choices
        }
        self.assertIn(f"finished:{self.product.pk}", choice_values)
        self.assertNotIn(f"finished:{foreign_product.pk}", choice_values)

    def test_wholesale_sale_uses_trade_price_receivable_and_purchase_cost(self):
        self.receive_product()
        customer = Customer.raw_objects.create(
            business=self.business,
            created_by=self.user,
            name="Corner Store",
        )
        CustomerProductPrice.raw_objects.create(
            business=self.business,
            created_by=self.user,
            customer=customer,
            finished_good=self.product,
            channel="distribution",
            price=Decimal("70"),
        )

        response = self.client.post(reverse("sale_add"), {
            "date": "2026-09-04",
            "customer_master": str(customer.pk),
            "customer": "",
            "transaction_type": "unpaid",
            "unpaid_description": "Seven-day trade credit",
            "payment_method": "Transfer",
            "account": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-finished_good": str(self.product.pk),
            "items-0-batch_qty": "0",
            "items-0-piece_qty": "2",
            "items-0-discount": "0",
        })

        self.assertRedirects(response, reverse("sales_list"), fetch_redirect_response=False)
        sale = Sale.raw_objects.get(business=self.business)
        item = sale.items.get()
        self.product.refresh_from_db()
        self.assertEqual(sale.source, "distribution_order")
        self.assertEqual(sale.customer_master, customer)
        self.assertEqual(sale.display_source, "Wholesale")
        self.assertEqual(item.price, Decimal("70"))
        self.assertEqual(item.unit_cost, Decimal("50"))
        self.assertEqual(self.product.stock, Decimal("10.00"))
        self.assertEqual(customer.outstanding_balance, Decimal("140"))

    def test_wholesale_vertical_blocks_production_but_keeps_stock_workflows(self):
        self.assertEqual(self.client.get(reverse("orders_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("procurement_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("inventory")).status_code, 200)
        self.assertEqual(self.client.get(reverse("sale_add")).status_code, 200)

    def test_retail_sale_stays_on_physical_store_channel(self):
        self.receive_product(quantity="5", unit_cost="40")
        self.business.vertical = Business.VERTICAL_RETAIL
        self.business.save(update_fields=["vertical"])

        response = self.client.post(reverse("sale_add"), {
            "date": "2026-09-04",
            "customer": "Walk-in",
            "transaction_type": "unpaid",
            "unpaid_description": "Promotional sample",
            "payment_method": "Cash",
            "account": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-finished_good": str(self.product.pk),
            "items-0-batch_qty": "0",
            "items-0-piece_qty": "1",
            "items-0-discount": "0",
        })

        self.assertRedirects(response, reverse("sales_list"), fetch_redirect_response=False)
        sale = Sale.raw_objects.get(business=self.business)
        self.assertEqual(sale.source, "walkin")
        self.assertEqual(sale.display_source, "Retail POS")
        self.assertEqual(sale.items.get().unit_cost, Decimal("40"))

    def test_vertical_lingo_converter_and_product_flow_are_rendered(self):
        general = Business(name="Factory", vertical=Business.VERTICAL_GENERAL)
        self.assertEqual(vertical_config(self.business)["recipe_label"], "Product Specification")
        self.assertEqual(vertical_config(general)["recipe_label"], "Formula / BOM")

        raw_form = self.client.get(reverse("raw_material_add"))
        self.assertContains(raw_form, "Unit converter & relationship calculator")
        self.assertContains(raw_form, "Material density (kg/L)")
        self.assertContains(raw_form, "Custom measured unit")
        self.assertContains(raw_form, "Manual stock count → purchase-unit fraction")
        self.assertContains(raw_form, "a 4 L package at 0.56 kg/L weighs 2.24 kg")
        self.assertContains(raw_form, 'id="stock-fraction-apply"')
        self.assertLess(
            raw_form.content.index(b'id="unit-converter-title"'),
            raw_form.content.index(b'id="id_usage_conversion_factor"'),
        )

        inventory = self.client.get(reverse("inventory"))
        self.assertContains(inventory, 'id="stock-history-flow-card"')
        self.assertContains(inventory, "Procurement · stock arrival · sale")
        self.assertContains(inventory, 'id="stock-history-flow-replay"')
        self.assertContains(inventory, "animateProductFlow")
