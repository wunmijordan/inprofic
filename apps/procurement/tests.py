from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from core.models import Business
from core.verticals import vertical_config
from inventory.models import FinishedGood, RawMaterial, StockMovement
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
        self.client.post(reverse("po_approve", args=[po.pk]), {f"item-{po.items.get().pk}-qty": quantity})
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


class ProcurementItemSummaryTests(TestCase):
    """The PO table uses the shared item summary + dialog, not a long inline list."""

    def setUp(self):
        self.business = Business.objects.create(name="Trade Hub", slug="trade-hub", vertical=Business.VERTICAL_WHOLESALE)
        self.user = CustomUser.objects.create_superuser(username="trade-admin", password="safe-password-123", fullname="Trade Admin")
        self.product = FinishedGood.raw_objects.create(
            business=self.business, created_by=self.user, name="Cooking Oil", unit="carton",
            stock=Decimal("0"), reorder_level=Decimal("0"), selling_price=Decimal("75"),
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()

    def make_po(self, item_count):
        po = PurchaseOrder.raw_objects.create(
            business=self.business, created_by=self.user, date=date(2026, 9, 4),
            supplier="Main Distributor", payment_status="unpaid",
        )
        for n in range(item_count):
            PurchaseOrderItem.objects.create(
                purchase_order=po, finished_good=self.product, qty=Decimal("2") + n, unit_cost=Decimal("50"),
            )
        return po

    def test_multi_item_po_shows_one_line_and_opens_the_shared_dialog(self):
        po = self.make_po(3)
        html = self.client.get(reverse("procurement_list")).content.decode()
        self.assertIn(f'data-items-dialog-open="po-items-{po.pk}"', html)
        self.assertIn("View 3 items", html)
        self.assertIn("+2 more", html)
        self.assertIn(f'id="po-items-{po.pk}"', html)
        self.assertIn('class="items-dialog', html)
        self.assertIn("Order total", html)
        self.assertIn("Whole-unit cost", html)
        # the old procurement-only dialog assets are gone
        self.assertNotIn("data-po-items-open", html)
        self.assertNotIn("po-items-dialog", html)
        self.assertEqual(html.count("document.addEventListener('click'"), 1)

    def test_single_item_po_has_no_dialog_or_button(self):
        po = self.make_po(1)
        html = self.client.get(reverse("procurement_list")).content.decode()
        self.assertNotIn(f'id="po-items-{po.pk}"', html)
        self.assertNotIn("View 1 item", html)
        self.assertIn("Cooking Oil", html)


class PurchaseApprovalTests(TestCase):
    """Receipt is authorized by a recorded review, never by editing a request."""

    setUp = DirectProductProcurementTests.setUp

    def request_order(self, quantity="10"):
        po = PurchaseOrder.raw_objects.create(
            business=self.business, created_by=self.user, date=date(2026, 9, 4),
            supplier="Main Distributor", payment_status="unpaid",
        )
        line = PurchaseOrderItem.objects.create(
            purchase_order=po, finished_good=self.product,
            qty=Decimal(quantity), unit_cost=Decimal("5"),
        )
        return po, line

    def test_receiving_unapproved_request_does_not_move_stock(self):
        po, line = self.request_order()
        self.client.post(reverse("po_receive", args=[po.pk]))
        self.product.refresh_from_db()
        po.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        self.assertEqual(po.status, "draft")
        self.assertFalse(StockMovement.raw_objects.filter(reference=f"PO-{po.pk}").exists())

    def test_existing_request_details_do_not_take_write_locks(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        po, line = self.request_order()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("po_approve", args=[po.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.name)
        self.assertFalse(any("FOR UPDATE" in query["sql"] for query in queries))

    def test_quantity_changes_require_reason(self):
        po, line = self.request_order()
        url = reverse("po_approve", args=[po.pk])
        response = self.client.post(url, {f"item-{line.pk}-qty": "8"})
        self.assertContains(response, "Give a reason")
        response = self.client.post(url, {f"item-{line.pk}-qty": "11"})
        self.assertContains(response, "Give a reason")
        po.refresh_from_db()
        self.assertEqual(po.status, "draft")

    def test_approval_retains_removed_line_and_receives_only_approved_quantity(self):
        po, line = self.request_order()
        removed = PurchaseOrderItem.objects.create(
            purchase_order=po, finished_good=self.product, qty=3, unit_cost=5,
        )
        response = self.client.post(reverse("po_approve", args=[po.pk]), {
            f"item-{line.pk}-qty": "8", f"item-{line.pk}-reason": "Budget limit",
            f"item-{removed.pk}-qty": "0", f"item-{removed.pk}-reason": "Already in stock",
        })
        self.assertEqual(response.status_code, 302)
        po.refresh_from_db(); line.refresh_from_db(); removed.refresh_from_db()
        self.assertEqual(po.status, "approved")
        self.assertEqual(po.approved_by, self.user)
        self.assertIsNotNone(po.approved_at)
        self.assertEqual(line.requested_qty, 10)
        self.assertEqual(line.qty, 8)
        self.assertEqual(removed.qty, 0)
        self.assertEqual(removed.requested_qty, 3)
        self.client.post(reverse("po_receive", args=[po.pk]))
        self.client.post(reverse("po_receive", args=[po.pk]))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(StockMovement.raw_objects.filter(reference=f"PO-{po.pk}").count(), 1)
        response = self.client.get(reverse("po_edit", args=[po.pk]))
        self.assertEqual(response.status_code, 302)

    def test_approval_cannot_reduce_below_paid_advance(self):
        from core.models import CashAccount, FinancialTransaction
        from core.services import record_cash
        po, line = self.request_order()
        account = CashAccount.raw_objects.create(business=self.business, name="Bank")
        record_cash(self.business, self.user, date=po.date, amount=45,
                    transaction_type=FinancialTransaction.OUTFLOW, category="Procurement",
                    description="Advance", reference=f"PO-{po.pk}", account=account)
        response = self.client.post(reverse("po_approve", args=[po.pk]), {
            f"item-{line.pk}-qty": "8", f"item-{line.pk}-reason": "Budget",
        })
        self.assertContains(response, "lower than payments already recorded")
        po.refresh_from_db()
        self.assertEqual(po.status, "draft")

    def test_other_business_request_is_inaccessible(self):
        other = Business.objects.create(name="Other", slug="other-review")
        po = PurchaseOrder.raw_objects.create(business=other, date=date(2026, 9, 4))
        self.assertEqual(self.client.get(reverse("po_approve", args=[po.pk])).status_code, 404)

    def test_procurement_editor_cannot_approve_without_administrator_role(self):
        from accounts.models import Role, UserBusiness
        editor = CustomUser.objects.create_user(username="purchase-editor", password="safe-password-123")
        from accounts.services import seed_business_roles
        seed_business_roles(self.business)
        role = Role.objects.get(business=self.business, key=CustomUser.ROLE_STOCK_KEEPER)
        UserBusiness.objects.create(user=editor, business=self.business, role=role, active=True)
        po, line = self.request_order()
        self.client.force_login(editor)
        response = self.client.post(reverse("po_approve", args=[po.pk]), {f"item-{line.pk}-qty": "10"})
        self.assertEqual(response.status_code, 403)
        po.refresh_from_db()
        self.assertEqual(po.status, "draft")

    def test_received_request_without_a_recorded_approval_reads_neutrally(self):
        from accounts.models import Role, UserBusiness
        from accounts.services import seed_business_roles
        editor = CustomUser.objects.create_user(username="purchase-viewer", password="safe-password-123")
        seed_business_roles(self.business)
        role = Role.objects.get(business=self.business, key=CustomUser.ROLE_STOCK_KEEPER)
        UserBusiness.objects.create(user=editor, business=self.business, role=role, active=True)
        po, _line = self.request_order()
        PurchaseOrder.raw_objects.filter(pk=po.pk).update(status="received", approved_at=None)
        self.client.force_login(editor)
        page = self.client.get(reverse("po_approve", args=[po.pk]))
        self.assertContains(page, "This request has already been received.")
        self.assertNotContains(page, "predates")
        self.assertNotContains(page, "approval workflow")


class PurchaseShareTests(TestCase):
    """WhatsApp text and snapshot of a request; compact approval page."""

    setUp = DirectProductProcurementTests.setUp
    request_order = PurchaseApprovalTests.request_order

    def make_request(self):
        po, line = self.request_order("50")
        extra = PurchaseOrderItem.objects.create(purchase_order=po, finished_good=self.product, qty=Decimal("4"), unit_cost=Decimal("12.50"))
        return po, line, extra

    def test_share_text_lists_lines_totals_and_a_review_link(self):
        from .share import share_text, summarize
        po, line, extra = self.make_request()
        text = share_text(summarize(po, self.business), "https://app.example/procurement/1/approve/")
        self.assertIn(f"*Purchase request #{po.pk}*", text)
        self.assertIn("Supplier: Main Distributor", text)
        self.assertIn("Status: Awaiting approval", text)
        self.assertIn(f"{self.product.name}: 50 cartons × {self.business.currency_symbol}5.00 = {self.business.currency_symbol}250.00", text)
        self.assertIn(f"*Total: {self.business.currency_symbol}300.00*", text)
        self.assertIn("Open INPROFIC to approve this purchase request (sign-in required):\nhttps://app.example/procurement/1/approve/", text)
        self.assertNotIn("Requested total", text)

    def test_share_text_shows_reductions_after_approval(self):
        po, line, extra = self.make_request()
        self.client.post(reverse("po_approve", args=[po.pk]), {
            f"item-{line.pk}-qty": "30", f"item-{line.pk}-reason": "Supplier short",
            f"item-{extra.pk}-qty": "0", f"item-{extra.pk}-reason": "Not needed"})
        from .share import share_text, summarize
        po.refresh_from_db()
        text = share_text(summarize(po, self.business))
        self.assertIn("(requested 50)", text)
        self.assertIn("removed (was 4", text)
        self.assertIn(f"Requested total: {self.business.currency_symbol}300.00", text)
        self.assertIn(f"*Total: {self.business.currency_symbol}150.00*", text)
        self.assertIn("Status: Approved", text)
        self.assertIn("Approved by", text)

    def test_snapshot_is_a_png_that_grows_with_the_request_and_caps_long_ones(self):
        import io
        from PIL import Image
        from .share import MAX_IMAGE_ROWS, render_snapshot, summarize
        po, _line, _extra = self.make_request()
        png = render_snapshot(summarize(po, self.business))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        short = Image.open(io.BytesIO(png))
        self.assertEqual(short.width, 1080)
        for _ in range(MAX_IMAGE_ROWS + 3):
            PurchaseOrderItem.objects.create(purchase_order=po, finished_good=self.product, qty=Decimal("1"), unit_cost=Decimal("1"))
        tall = Image.open(io.BytesIO(render_snapshot(summarize(po, self.business))))
        self.assertGreater(tall.height, short.height)
        self.assertLess(tall.height, short.height + 20 * 100)       # rows beyond the cap are summarised, not drawn

    def test_snapshot_fonts_can_draw_the_currency_and_separator_glyphs(self):
        from reportlab.pdfbase.ttfonts import TTFont
        from .share import FONT_BOLD, FONT_REGULAR, _FONT_DIR
        for name in (FONT_REGULAR, FONT_BOLD):
            mapped = TTFont(name, str(_FONT_DIR / name)).face.charToGlyph
            for char in "₦×·—":
                self.assertIn(ord(char), mapped, f"{name} cannot draw {char!r}")

    def test_snapshot_view_serves_png_privately(self):
        po, _line, _extra = self.make_request()
        response = self.client.get(reverse("po_share_image", args=[po.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn(f"purchase-request-{po.pk}.png", response["Content-Disposition"])

    def test_snapshot_is_tenant_and_permission_scoped(self):
        other = Business.objects.create(name="Other", slug="other-share")
        foreign = PurchaseOrder.raw_objects.create(business=other, date=date(2026, 9, 4))
        self.assertEqual(self.client.get(reverse("po_share_image", args=[foreign.pk])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("po_share_image", args=[1])).status_code, 302)
        from accounts.models import Role, UserBusiness
        from accounts.services import seed_business_roles
        outsider = CustomUser.objects.create_user(username="no-procurement", password="safe-password-123")
        seed_business_roles(self.business)
        UserBusiness.objects.create(user=outsider, business=self.business, active=True,
                                    role=Role.objects.get(business=self.business, key=CustomUser.ROLE_POS_OPERATOR))
        po, _line, _extra = self.make_request()
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("po_share_image", args=[po.pk])).status_code, 403)

    def test_approval_review_keeps_sharing_in_the_request_form(self):
        po, line, extra = self.make_request()
        page = self.client.get(reverse("po_approve", args=[po.pk]))
        self.assertNotContains(page, 'data-snapshot-share')
        share_page = self.client.get(reverse("po_edit", args=[po.pk]))
        self.assertContains(share_page, 'data-snapshot-share')
        self.assertContains(share_page, "Share snapshot")
        self.assertContains(share_page, "Copy text")
        self.assertContains(page, "Approve request")
        self.assertContains(page, "Remove")
        self.assertContains(page, f'name="item-{line.pk}-qty"')
        self.assertContains(page, "Approved total")
        # Reason boxes start collapsed but are present for the server to validate.
        self.assertContains(page, f'name="item-{line.pk}-reason"')
        self.assertContains(page, "data-collapsed")

    def test_page_keeps_reason_open_and_shows_the_error_when_approval_is_rejected(self):
        po, line, extra = self.make_request()
        page = self.client.post(reverse("po_approve", args=[po.pk]), {f"item-{line.pk}-qty": "20", f"item-{extra.pk}-qty": "4"})
        self.assertContains(page, "Give a reason for changing or removing this item.")
        po.refresh_from_db()
        self.assertEqual(po.status, "draft")

    def test_share_text_is_escaped_in_the_page(self):
        po, line, extra = self.make_request()
        PurchaseOrder.raw_objects.filter(pk=po.pk).update(supplier='</script><script>alert(1)</script>')
        page = self.client.get(reverse("po_edit", args=[po.pk]))
        self.assertNotContains(page, "</script><script>alert(1)")

    def test_read_only_members_see_the_same_snapshot_controls_without_editing(self):
        po, line, extra = self.make_request()
        from accounts.models import Role, UserBusiness
        from accounts.services import seed_business_roles
        viewer = CustomUser.objects.create_user(username="proc-viewer", password="safe-password-123")
        seed_business_roles(self.business)
        UserBusiness.objects.create(user=viewer, business=self.business, active=True,
                                    role=Role.objects.get(business=self.business, key=CustomUser.ROLE_STOCK_KEEPER))
        self.client.force_login(viewer)
        page = self.client.get(reverse("po_approve", args=[po.pk]))
        self.assertNotContains(page, "Share on WhatsApp")
        share_page = self.client.get(reverse("po_edit", args=[po.pk]) + "?share=1")
        self.assertContains(share_page, "Share snapshot")
        self.assertNotContains(page, "Approve request")
        self.assertNotContains(page, f'name="item-{line.pk}-qty"')

    def test_share_link_renders_a_preview_without_editable_or_disabled_form(self):
        po, line, extra = self.make_request()
        PurchaseOrder.objects.filter(pk=po.pk).update(status="approved")
        response = self.client.get(reverse("po_edit", args=[po.pk]) + "?share=1")
        self.assertTemplateUsed(response, "procurement/po_preview.html")
        self.assertContains(response, "data-request-preview")
        self.assertContains(response, line.item_name)
        self.assertContains(response, "Whole-unit cost")
        self.assertContains(response, "data-ui-snapshot-menu")
        self.assertNotContains(response, "purchase-request-form")
        self.assertNotContains(response, "<fieldset")
        self.assertNotContains(response, 'name="items-TOTAL_FORMS"')
        self.assertNotContains(response, "Save and share")



class ProcurementStockVisibilityTests(TestCase):
    """Current stock of requested items in the form, approval page, text and snapshot."""

    setUp = DirectProductProcurementTests.setUp
    request_order = PurchaseApprovalTests.request_order

    def make_request(self):
        FinishedGood.objects.filter(pk=self.product.pk).update(stock=Decimal("3"), reorder_level=Decimal("10"))
        po, line = self.request_order("50")
        extra = PurchaseOrderItem.objects.create(purchase_order=po, finished_good=self.product, qty=Decimal("4"), unit_cost=Decimal("12.50"))
        return po, line, extra

    def raw(self, **fields):
        from inventory.models import RawMaterial
        defaults = dict(business=self.business, name="Flour", purchase_unit="bag", usage_unit="kg", package_qty=Decimal("50"),
                        usage_conversion_factor=Decimal("1"), stock=Decimal("130"), reorder_level=Decimal("40"))
        return RawMaterial.objects.create(**{**defaults, **fields})

    def test_status_levels_and_pack_breakdown(self):
        from .stockview import stock_line, stock_status
        flour = self.raw()
        status = stock_status(flour)
        self.assertEqual((status["status"], status["text"], status["packs"]), ("ok", "130 kg", "2 bags + 30 kg"))
        for stock, expected in (("0", "out"), ("40", "low"), ("55", "warning"), ("500", "ok")):
            flour.stock = Decimal(stock)
            self.assertEqual(stock_status(flour)["status"], expected, stock)
        flour.reorder_level = Decimal("0")
        flour.stock = Decimal("5")
        self.assertEqual(stock_status(flour)["status"], "ok")
        self.assertNotIn("no reorder level set", stock_line(stock_status(flour)))
        self.assertEqual(stock_status(self.product)["unit"], self.product.unit)

    def test_untracked_resale_products_say_so(self):
        from .stockview import stock_status
        FinishedGood.objects.filter(pk=self.product.pk).update(stock=None)
        self.product.refresh_from_db()
        status = stock_status(self.product)
        self.assertEqual((status["tracked"], status["status"]), (False, "untracked"))

    def test_share_text_omits_stock_and_keeps_purchase_details_and_sign_in_link(self):
        from .share import share_text, summarize
        po, line, extra = self.make_request()
        url = "https://app.example/procurement/9/approve/"
        text = share_text(summarize(po, self.business, url))
        self.assertNotIn("Stock now", text)
        self.assertNotIn("Stock as of", text)
        self.assertNotIn("reorder at", text)
        self.assertIn("Purchase request", text)
        self.assertIn("Group total", text)
        self.assertIn("(sign-in required)", text)
        self.assertTrue(text.rstrip().endswith(url))
        approved = text.replace("approve this purchase request", "")
        self.assertNotIn("view this", approved)

    def test_non_pending_requests_offer_view_not_approve(self):
        from .share import share_text, summarize
        po, line, extra = self.make_request()
        PurchaseOrder.raw_objects.filter(pk=po.pk).update(status="received")
        po.refresh_from_db()
        text = share_text(summarize(po, self.business, "https://app.example/x/"))
        self.assertIn("Open INPROFIC to view this purchase request (sign-in required)", text)

    def test_snapshot_with_a_link_is_taller_and_still_a_valid_png(self):
        import io
        from PIL import Image
        from .share import render_snapshot, summarize
        po, _l, _e = self.make_request()
        plain = Image.open(io.BytesIO(render_snapshot(summarize(po, self.business))))
        linked = Image.open(io.BytesIO(render_snapshot(summarize(po, self.business, "https://app.example/procurement/1/approve/"))))
        self.assertGreater(linked.height, plain.height)
        self.assertEqual(linked.width, 1080)

    def test_snapshot_view_inline_by_default_and_attachment_on_request(self):
        po, _l, _e = self.make_request()
        inline = self.client.get(reverse("po_share_image", args=[po.pk]))
        self.assertTrue(inline["Content-Disposition"].startswith("inline"))
        saved = self.client.get(reverse("po_share_image", args=[po.pk]) + "?download=1")
        self.assertTrue(saved["Content-Disposition"].startswith("attachment"))

    def test_approval_page_shows_stock_and_a_live_projection_hook(self):
        po, line, extra = self.make_request()
        page = self.client.get(reverse("po_approve", args=[po.pk]))
        self.assertContains(page, "Stock now:")
        self.assertContains(page, "pa-stock is-low")
        self.assertContains(page, "data-after")
        share_page = self.client.get(reverse("po_edit", args=[po.pk]))
        self.assertContains(share_page, "View snapshot")
        self.assertContains(share_page, "Save image")

    def test_request_form_exposes_stock_for_every_selectable_item(self):
        self.make_request()
        flour = self.raw()
        page = self.client.get(reverse("po_add"))
        self.assertContains(page, 'id="mat-stock"')
        self.assertContains(page, "data-stock-note")
        levels = page.context["stock_levels"]
        self.assertIn(f"finished:{self.product.pk}", levels)
        self.assertEqual(levels[f"raw:{flour.pk}"]["packs"], "2 bags + 30 kg")
        self.assertEqual(levels[f"finished:{self.product.pk}"]["status"], "low")
        self.assertContains(page, "Stock now: ")

    def test_stock_is_only_for_this_business(self):
        other = Business.objects.create(name="Elsewhere", slug="elsewhere-stock")
        from inventory.models import RawMaterial
        foreign = RawMaterial.objects.create(business=other, name="Secret", purchase_unit="bag", usage_unit="kg", stock=Decimal("9"))
        page = self.client.get(reverse("po_add"))
        self.assertNotIn(f"raw:{foreign.pk}", page.context["stock_levels"])
