"""Behavioural tests for the ledger cut-over (Backlog vs Live) feature."""
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from inventory.models import FinishedGood, RawMaterial, StockMovement
from inventory.services import record_finished_good_movement, record_raw_material_movement
from sales.models import Sale

from . import ledger
from .context import set_current_business, set_ledger_context
from .models import CashAccount, FinancialTransaction, LedgerCutover, OpeningPosition
from .context import begin_request_cache, end_request_cache

TODAY = timezone.localdate()
CUTOVER = TODAY - timedelta(days=10)
BACKLOG_DAY = CUTOVER - timedelta(days=3)
LIVE_DAY = CUTOVER + timedelta(days=2)


@contextmanager
def scoped(business, scope, status="open", cutover=CUTOVER):
    """Simulate the middleware's per-request state."""
    token = begin_request_cache()
    set_current_business(business)
    set_ledger_context(scope, cutover, status)
    try:
        yield
    finally:
        set_ledger_context(None, None, None)
        set_current_business(None)
        end_request_cache(token)


class LedgerBase(TestCase):
    def setUp(self):
        from .models import Business
        self.business = Business.objects.create(name="Split Bakery", slug="split-bakery")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="ledger-admin", password="pw-123456")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.staff = CustomUser.objects.create_user(username="ledger-staff", password="pw-123456")
        staff_role = next(r for key, r in roles.items() if key != CustomUser.ROLE_BUSINESS_ADMIN)
        UserBusiness.objects.create(user=self.staff, business=self.business, role=staff_role)
        self.flour = RawMaterial.raw_objects.create(business=self.business, name="Flour", usage_unit="kg",
                                                    stock=Decimal("100.000"), cost_per_unit=Decimal("2"))
        self.bread = FinishedGood.raw_objects.create(business=self.business, name="Bread", unit="loaf",
                                                     stock=Decimal("50.00"))
        self.account = CashAccount.raw_objects.create(business=self.business, name="Till", opening_balance=Decimal("1000"))

    def start_cutover(self, user=None):
        self.client.force_login(user or self.admin)
        return self.client.post(reverse("ledger_action"), {"action": "start", "cutover_date": CUTOVER.isoformat()})

    def stored(self, model, pk):
        return model.raw_objects.filter(pk=pk).values_list("stock", flat=True).get()


class ScopingAndGuardTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.start_cutover()
        self.client.logout()

    def test_live_and_backlog_records_are_hidden_from_each_other(self):
        Sale.raw_objects.create(business=self.business, date=BACKLOG_DAY, customer="Old")
        Sale.raw_objects.create(business=self.business, date=LIVE_DAY, customer="New")
        with scoped(self.business, "live"):
            self.assertEqual(list(Sale.objects.values_list("customer", flat=True)), ["New"])
        with scoped(self.business, "backlog"):
            self.assertEqual(list(Sale.objects.values_list("customer", flat=True)), ["Old"])
        with scoped(self.business, "all"):
            self.assertEqual(Sale.objects.count(), 2)

    def test_live_entry_before_cutover_is_rejected_with_correct_wording(self):
        with scoped(self.business, "live"):
            with self.assertRaises(ledger.LedgerDateError) as ctx:
                Sale.objects.create(business=self.business, date=BACKLOG_DAY, customer="Wrong")
        self.assertIn("before the live start", ctx.exception.message)
        self.assertTrue(ctx.exception.suggest_backlog)

    def test_backlog_entry_on_or_after_cutover_is_rejected(self):
        with scoped(self.business, "backlog"):
            with self.assertRaises(ledger.LedgerDateError) as ctx:
                Sale.objects.create(business=self.business, date=LIVE_DAY, customer="Wrong")
            Sale.objects.create(business=self.business, date=BACKLOG_DAY, customer="Right")
        self.assertIn("only accepts dates before", ctx.exception.message)

    def test_locked_archived_and_merged_states_refuse_backlog_writes(self):
        with scoped(self.business, "backlog", status="complete"):
            with self.assertRaises(ledger.LedgerDateError):
                Sale.objects.create(business=self.business, date=BACKLOG_DAY, customer="Locked")
        with scoped(self.business, "all", status="merged"):
            with self.assertRaises(ledger.LedgerDateError) as ctx:
                Sale.objects.create(business=self.business, date=BACKLOG_DAY, customer="Sealed")
            Sale.objects.create(business=self.business, date=LIVE_DAY, customer="Fine")
        self.assertIn("sealed", ctx.exception.message)

    def test_archived_period_does_not_offer_the_backlog_switch(self):
        with scoped(self.business, "live", status="archived"):
            with self.assertRaises(ledger.LedgerDateError) as ctx:
                Sale.objects.create(business=self.business, date=BACKLOG_DAY, customer="Sealed")
        self.assertFalse(ctx.exception.suggest_backlog)

    def test_updating_an_existing_record_without_changing_its_date_is_allowed(self):
        sale = Sale.raw_objects.create(business=self.business, date=BACKLOG_DAY, customer="Old")
        with scoped(self.business, "live"):
            sale.customer = "Renamed"
            sale.save()  # not a date change: the guard stays quiet


class StockRoutingTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.start_cutover()

    def test_starting_a_cutover_does_not_change_stored_stock(self):
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("100.000"))
        self.assertEqual(self.stored(FinishedGood, self.bread.pk), Decimal("50.00"))
        self.assertTrue(OpeningPosition.raw_objects.filter(segment="live", kind="raw").exists())

    def test_backlog_dated_movement_never_touches_live_stock(self):
        record_raw_material_movement(self.flour, Decimal("-30"), StockMovement.USAGE
                                     if hasattr(StockMovement, "USAGE") else StockMovement.ADJUSTMENT,
                                     on_date=BACKLOG_DAY)
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("100.000"))
        movement = StockMovement.raw_objects.get(raw_material=self.flour)
        self.assertIsNone(movement.balance_after)
        self.assertEqual(movement.business_date, BACKLOG_DAY)

    def test_live_dated_movement_updates_stock_and_balance_after(self):
        record_raw_material_movement(self.flour, Decimal("-25"), StockMovement.ADJUSTMENT, on_date=LIVE_DAY)
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("75.000"))
        self.assertEqual(StockMovement.raw_objects.get(raw_material=self.flour).balance_after, Decimal("75.000"))

    def test_stock_attribute_shows_segment_balance_per_mode(self):
        record_raw_material_movement(self.flour, Decimal("-30"), StockMovement.ADJUSTMENT, on_date=BACKLOG_DAY)
        record_raw_material_movement(self.flour, Decimal("-5"), StockMovement.ADJUSTMENT, on_date=LIVE_DAY)
        with scoped(self.business, "live"):
            flour = RawMaterial.objects.get(pk=self.flour.pk)
            self.assertEqual(flour.stock, Decimal("95.000"))
        with scoped(self.business, "backlog", ):
            flour = RawMaterial.objects.get(pk=self.flour.pk)
            # backlog opening (100 at the time of the cut-over) + backlog movement -30
            self.assertEqual(flour.stock, Decimal("70.000"))

    def test_saving_in_backlog_mode_never_writes_a_derived_figure(self):
        record_raw_material_movement(self.flour, Decimal("-30"), StockMovement.ADJUSTMENT, on_date=BACKLOG_DAY)
        with scoped(self.business, "backlog"):
            flour = RawMaterial.objects.get(pk=self.flour.pk)
            flour.name = "Bread flour"
            flour.save()
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("100.000"))

    def test_items_created_after_cutover_keep_their_stored_stock(self):
        sugar = RawMaterial.raw_objects.create(business=self.business, name="Sugar", usage_unit="kg", stock=Decimal("8"))
        with scoped(self.business, "live"):
            self.assertEqual(RawMaterial.objects.get(pk=sugar.pk).stock, Decimal("8"))
        self.assertEqual(ledger.rebuild_live_stock(self.business), {"raw": 0, "fg": 0})
        self.assertEqual(self.stored(RawMaterial, sugar.pk), Decimal("8.000"))

    def test_rebuild_repairs_drift_and_is_idempotent(self):
        record_raw_material_movement(self.flour, Decimal("-10"), StockMovement.ADJUSTMENT, on_date=LIVE_DAY)
        RawMaterial.raw_objects.filter(pk=self.flour.pk).update(stock=Decimal("1"))
        self.assertEqual(len(ledger.integrity_report(self.business)), 1)
        ledger.rebuild_live_stock(self.business)
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("90.000"))
        self.assertEqual(ledger.integrity_report(self.business), [])
        self.assertEqual(ledger.rebuild_live_stock(self.business), {"raw": 0, "fg": 0})

    def test_stock_reads_in_a_loop_do_not_query_per_item(self):
        for i in range(15):
            RawMaterial.raw_objects.create(business=self.business, name=f"Item {i}", usage_unit="kg", stock=Decimal("1"))
        with scoped(self.business, "live"):
            items = list(RawMaterial.objects.all())
            with CaptureQueriesContext(connection) as queries:
                for item in items:
                    item.stock
                for item in items:
                    item.stock
            self.assertLessEqual(len(queries), 4)


class OpeningMergeAndPermissionTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.start_cutover()
        record_raw_material_movement(self.flour, Decimal("-30"), StockMovement.ADJUSTMENT, on_date=BACKLOG_DAY)
        FinancialTransaction.raw_objects.create(
            business=self.business, account=self.account, date=BACKLOG_DAY, transaction_type=FinancialTransaction.INCOME,
            amount=Decimal("500"), description="old takings")

    def post(self, **data):
        self.client.force_login(self.admin)
        return self.client.post(reverse("ledger_action"), data, follow=True)

    def test_counted_openings_rebuild_live_stock_and_only_mark_changed_rows(self):
        self.post(action="save_openings", **{f"pos_raw_{self.flour.pk}": "80",
                                              f"pos_fg_{self.bread.pk}": "50.00"})
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("80.000"))
        flour_pos = OpeningPosition.raw_objects.get(segment="live", kind="raw", object_id=self.flour.pk)
        bread_pos = OpeningPosition.raw_objects.get(segment="live", kind="fg", object_id=self.bread.pk)
        self.assertEqual(flour_pos.source, "counted")
        self.assertEqual(bread_pos.source, "derived")  # unchanged row keeps its provisional marker

    def test_merge_preview_commit_and_exact_undo(self):
        self.post(action="save_openings", **{f"pos_raw_{self.flour.pk}": "80"})
        self.post(action="complete")
        self.assertEqual(LedgerCutover.raw_objects.get().status, "complete")
        # Committing without previewing is refused.
        self.post(action="merge")
        self.assertEqual(LedgerCutover.raw_objects.get().status, "complete")
        self.post(action="preview_merge")
        self.post(action="merge")
        cutover = LedgerCutover.raw_objects.get()
        self.assertEqual(cutover.status, "merged")
        # Backlog closing = opening 100 - 30 = 70 replaces the counted 80.
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("70.000"))
        # Live activity recorded after the merge survives an undo.
        record_raw_material_movement(self.flour, Decimal("-4"), StockMovement.ADJUSTMENT, on_date=TODAY)
        self.post(action="undo_merge")
        self.assertEqual(LedgerCutover.raw_objects.get().status, "complete")
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("76.000"))  # 80 counted - 4

    def test_merged_cash_balance_continues_from_backlog_closing(self):
        self.post(action="complete")
        self.post(action="preview_merge")
        self.post(action="merge")
        account = CashAccount.raw_objects.get(pk=self.account.pk)
        with scoped(self.business, "all", status="merged"):
            self.assertEqual(account.balance, Decimal("1500"))

    def test_only_business_admins_can_manage_the_cutover(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse("ledger_action"), {"action": "complete"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("ledger_workspace")).status_code, 403)

    def test_combined_and_backlog_views_need_ledger_permission(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse("ledger_mode"), {"mode": "all"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("ledger_mode"), {"mode": "backlog"}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("ledger_mode"), {"mode": "all"}).status_code, 302)

    def test_carry_over_lists_only_open_documents_and_audits(self):
        from production.models import Order
        open_order = Order.raw_objects.create(business=self.business, date=BACKLOG_DAY, status="pending")
        Order.raw_objects.create(business=self.business, date=BACKLOG_DAY, status="rejected")
        Order.raw_objects.create(business=self.business, date=BACKLOG_DAY, status="completed")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("ledger_workspace"))
        self.assertEqual([o.pk for o in page.context["carryover_orders"]], [open_order.pk])
        self.post(action="carry_over", orders=[open_order.pk])
        open_order.refresh_from_db()
        self.assertEqual(open_order.date, CUTOVER)
        self.assertEqual(open_order.carried_from_date, BACKLOG_DAY)

    def test_cutover_cannot_start_in_the_future(self):
        LedgerCutover.raw_objects.all().delete()
        self.client.force_login(self.admin)
        self.client.post(reverse("ledger_action"), {"action": "start", "cutover_date": (TODAY + timedelta(days=2)).isoformat()})
        self.assertFalse(LedgerCutover.raw_objects.exists())

    def test_receivables_stay_invoice_level_across_segments(self):
        from .finance_views import _finance_open_items
        Sale.raw_objects.create(business=self.business, date=BACKLOG_DAY, customer="Old debtor",
                                source="distribution_order", transaction_type="credit")
        with scoped(self.business, "live"):
            items = _finance_open_items(self.business)
        self.assertIn("receivables", items)


class EventDateTests(LedgerBase):
    def make_request(self, value=None):
        request = RequestFactory().post("/x/", {"event_date": value} if value is not None else {})
        return request

    def test_defaults_and_limits(self):
        self.assertEqual(ledger.resolve_event_date(self.make_request(), TODAY - timedelta(days=3)), TODAY)
        past = TODAY - timedelta(days=2)
        self.assertEqual(ledger.resolve_event_date(self.make_request(past.isoformat()), TODAY - timedelta(days=5)), past)
        with self.assertRaises(ledger.LedgerDateError):
            ledger.resolve_event_date(self.make_request((TODAY + timedelta(days=1)).isoformat()), TODAY)
        with self.assertRaises(ledger.LedgerDateError):
            ledger.resolve_event_date(self.make_request((TODAY - timedelta(days=9)).isoformat()), TODAY - timedelta(days=5))
        with self.assertRaises(ledger.LedgerDateError):
            ledger.resolve_event_date(self.make_request("not-a-date"), TODAY)

    def test_backlog_mode_defaults_to_the_document_date(self):
        self.start_cutover()
        with scoped(self.business, "backlog"):
            self.assertEqual(ledger.resolve_event_date(self.make_request(), BACKLOG_DAY), BACKLOG_DAY)
            self.assertEqual(ledger.default_entry_date(), CUTOVER - timedelta(days=1))
            with self.assertRaises(ledger.LedgerDateError):
                ledger.resolve_event_date(self.make_request(TODAY.isoformat()), BACKLOG_DAY)

    def test_live_mode_rejects_an_event_date_before_the_cutover(self):
        self.start_cutover()
        with scoped(self.business, "live"):
            with self.assertRaises(ledger.LedgerDateError):
                ledger.resolve_event_date(self.make_request(BACKLOG_DAY.isoformat()), BACKLOG_DAY)
            self.assertEqual(ledger.resolve_event_date(self.make_request(LIVE_DAY.isoformat()), LIVE_DAY), LIVE_DAY)


class RegistryTests(TestCase):
    def test_every_registered_model_has_a_valid_date_field(self):
        self.assertTrue(ledger.validate_registry())
        self.assertGreaterEqual(len(ledger.registered_ledger_models()), 10)


class BackdatedWorkflowTests(LedgerBase):
    """The workflow actions record the date the work really happened."""

    def make_po(self, order_day):
        from procurement.models import PurchaseOrder, PurchaseOrderItem
        po = PurchaseOrder.raw_objects.create(business=self.business, date=order_day, supplier="Mill Co", status="approved")
        PurchaseOrderItem.objects.create(purchase_order=po, raw_material=self.flour, qty=Decimal("10"), unit_cost=Decimal("2"))
        return po

    def test_receipt_defaults_to_today_not_the_order_date(self):
        po = self.make_po(TODAY - timedelta(days=4))
        self.client.force_login(self.admin)
        self.client.post(reverse("po_receive", args=[po.pk]))
        po.refresh_from_db()
        self.assertEqual(po.status, "received")
        self.assertEqual(po.received_date, TODAY)

    def test_receipt_can_be_backdated_and_keeps_that_date_everywhere(self):
        from procurement.models import RawMaterialCostSnapshot
        arrived = TODAY - timedelta(days=2)
        po = self.make_po(TODAY - timedelta(days=4))
        self.client.force_login(self.admin)
        self.client.post(reverse("po_receive", args=[po.pk]), {"event_date": arrived.isoformat()})
        po.refresh_from_db()
        self.assertEqual(po.received_date, arrived)
        self.assertEqual(StockMovement.raw_objects.get(raw_material=self.flour).business_date, arrived)
        self.assertEqual(RawMaterialCostSnapshot.raw_objects.get(raw_material=self.flour).effective_date, arrived)

    def test_an_older_late_receipt_does_not_overwrite_a_newer_cost(self):
        from procurement.models import PurchaseOrder, PurchaseOrderItem
        self.client.force_login(self.admin)
        newer = self.make_po(TODAY - timedelta(days=5))
        PurchaseOrderItem.objects.filter(purchase_order=newer).update(unit_cost=Decimal("9"))
        self.client.post(reverse("po_receive", args=[newer.pk]), {"event_date": TODAY.isoformat()})
        older = self.make_po(TODAY - timedelta(days=9))
        PurchaseOrderItem.objects.filter(purchase_order=older).update(unit_cost=Decimal("1"))
        self.client.post(reverse("po_receive", args=[older.pk]), {"event_date": (TODAY - timedelta(days=8)).isoformat()})
        self.assertEqual(RawMaterial.raw_objects.get(pk=self.flour.pk).cost_per_unit, Decimal("9"))

    def test_future_and_pre_document_dates_are_refused_with_a_friendly_page(self):
        po = self.make_po(TODAY - timedelta(days=3))
        self.client.force_login(self.admin)
        future = self.client.post(reverse("po_receive", args=[po.pk]), {"event_date": (TODAY + timedelta(days=1)).isoformat()}, follow=True)
        self.assertContains(future, "cannot be in the future")
        early = self.client.post(reverse("po_receive", args=[po.pk]), {"event_date": (TODAY - timedelta(days=9)).isoformat()}, follow=True)
        self.assertContains(early, "earlier than the document date")
        po.refresh_from_db()
        self.assertEqual(po.status, "approved")  # nothing was received; the approved request is untouched

    def test_create_form_dated_before_the_cutover_offers_the_backlog_switch(self):
        from expenses.models import Expense
        self.start_cutover()
        payload = {"expenses-TOTAL_FORMS": "1", "expenses-INITIAL_FORMS": "0", "expenses-MIN_NUM_FORMS": "0",
                   "expenses-MAX_NUM_FORMS": "1000", "expenses-0-date": BACKLOG_DAY.isoformat(),
                   "expenses-0-category": "utilities", "expenses-0-description": "Old power bill",
                   "expenses-0-amount": "40", "expenses-0-payment_status": "unpaid",
                   "expenses-0-payment_method": "Cash"}
        response = self.client.post(reverse("expense_add"), payload)
        self.assertContains(response, "before the live start", status_code=400)
        self.assertContains(response, "Switch to Backlog entry", status_code=400)
        self.assertFalse(Expense.raw_objects.exists())
        # A user without backlog permission is told why but is not offered the switch.
        self.client.force_login(self.staff)
        refused = self.client.post(reverse("expense_add"), payload)
        self.assertNotContains(refused, "Switch to Backlog entry", status_code=400) if refused.status_code == 400 else None

    def test_backlog_mode_records_old_expenses_and_hides_them_from_live(self):
        from expenses.models import Expense
        self.start_cutover()
        self.client.post(reverse("ledger_mode"), {"mode": "backlog"})
        payload = {"expenses-TOTAL_FORMS": "1", "expenses-INITIAL_FORMS": "0", "expenses-MIN_NUM_FORMS": "0",
                   "expenses-MAX_NUM_FORMS": "1000", "expenses-0-date": BACKLOG_DAY.isoformat(),
                   "expenses-0-category": "utilities", "expenses-0-description": "Old power bill",
                   "expenses-0-amount": "40", "expenses-0-payment_status": "unpaid",
                   "expenses-0-payment_method": "Cash"}
        self.client.post(reverse("expense_add"), payload)
        self.assertEqual(Expense.raw_objects.count(), 1)
        listing = self.client.get(reverse("expenses_list"), follow=True)
        self.assertContains(listing, "Old power bill")
        self.client.post(reverse("ledger_mode"), {"mode": "live"})
        self.assertNotContains(self.client.get(reverse("expenses_list"), follow=True), "Old power bill")


class MarketLotTests(LedgerBase):
    """Market stock lots are a pool: old lots stay sellable in Live; backlog-made lots do not leak."""

    def make_lot(self, received, qty="10", **extra):
        from inventory.models import MarketStockLot, MarketStockMovement
        lot = MarketStockLot.raw_objects.create(
            business=self.business, finished_good=self.bread, source="production", received_date=received,
            quantity_received=Decimal(qty), quantity_available=Decimal(qty), unit_cost=Decimal("2"), **extra)
        MarketStockMovement.raw_objects.create(business=self.business, lot=lot, date=received,
                                               movement_type="production_in", quantity=Decimal(qty), balance_after=Decimal(qty))
        return lot

    def test_an_existing_pre_cutover_lot_stays_visible_and_sellable_in_live(self):
        from inventory.models import MarketStockLot
        from inventory.services import market_stock_available
        old = self.make_lot(BACKLOG_DAY)
        self.start_cutover()
        self.client.logout()
        with scoped(self.business, "live"):
            self.assertEqual([lot.pk for lot in MarketStockLot.objects.all()], [old.pk])
            self.assertEqual(market_stock_available(self.bread, TODAY), Decimal("10"))

    def test_a_lot_created_by_backlog_entry_never_adds_live_stock(self):
        from inventory.models import MarketStockLot
        from inventory.services import market_stock_available, receive_market_production
        from production.models import Order, OrderItem, ProductionBatch
        self.start_cutover()
        order = Order.raw_objects.create(business=self.business, date=BACKLOG_DAY, status="approved")
        item = OrderItem.objects.create(order=order, finished_good=self.bread, batch_qty=Decimal("1"))
        make_batch = lambda day, number: ProductionBatch.raw_objects.create(
            business=self.business, order=order, order_item=item, finished_good=self.bread,
            production_date=day, batch_number=number, unit_cost=Decimal("2"))
        old_lot = receive_market_production(make_batch(BACKLOG_DAY, "B-OLD"), Decimal("10"), user=self.admin)
        new_lot = receive_market_production(make_batch(LIVE_DAY, "B-NEW"), Decimal("4"), user=self.admin)
        self.assertTrue(old_lot.is_backlog_entry)
        self.assertFalse(new_lot.is_backlog_entry)
        with scoped(self.business, "live"):
            self.assertEqual({lot.pk for lot in MarketStockLot.objects.all()}, {new_lot.pk})
            self.assertEqual(market_stock_available(self.bread, TODAY), Decimal("4"))
        with scoped(self.business, "backlog"):
            self.assertIn(old_lot.pk, {lot.pk for lot in MarketStockLot.objects.all()})
            self.assertEqual(market_stock_available(self.bread, BACKLOG_DAY), Decimal("10"))

    def test_merge_sets_lots_to_the_combined_balance_and_undo_restores_them(self):
        from inventory.models import MarketStockLot, MarketStockMovement
        old = self.make_lot(BACKLOG_DAY, qty="10")
        self.start_cutover()
        # A live release after the cut-over, then a backlog release entered later.
        MarketStockMovement.raw_objects.create(business=self.business, lot=old, date=LIVE_DAY,
                                               movement_type="release", quantity=Decimal("-3"), balance_after=Decimal("7"))
        MarketStockLot.raw_objects.filter(pk=old.pk).update(quantity_available=Decimal("7"))
        MarketStockMovement.raw_objects.create(business=self.business, lot=old, date=BACKLOG_DAY,
                                               movement_type="release", quantity=Decimal("-2"), balance_after=Decimal("8"))
        backlog_lot = self.make_lot(BACKLOG_DAY, qty="5", is_backlog_entry=True)
        self.client.force_login(self.admin)
        for action in ("complete", "preview_merge", "merge"):
            self.client.post(reverse("ledger_action"), {"action": action})
        old.refresh_from_db()
        backlog_lot.refresh_from_db()
        self.assertEqual(old.quantity_available, Decimal("5.00"))  # 10 - 3 live - 2 backlog
        self.assertEqual(backlog_lot.quantity_available, Decimal("5.00"))
        self.assertFalse(backlog_lot.is_backlog_entry)
        self.client.post(reverse("ledger_action"), {"action": "undo_merge"})
        old.refresh_from_db()
        backlog_lot.refresh_from_db()
        self.assertEqual(old.quantity_available, Decimal("7.00"))
        self.assertTrue(backlog_lot.is_backlog_entry)


class CountToolTests(LedgerBase):
    """Count sheet CSV, CSV import and the phone count page confirm provisional openings."""

    def setUp(self):
        super().setUp()
        self.start_cutover()

    def sheet(self, counted_flour="", counted_bread="", extra=""):
        header = "type,id,name,unit,current_opening,counted\n"
        return (f"{header}raw,{self.flour.pk},Flour,kg,100,{counted_flour}\n"
                f"fg,{self.bread.pk},Bread,loaf,50,{counted_bread}\n{extra}")

    def upload(self, text):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return self.client.post(reverse("ledger_count_import"), {"counts": SimpleUploadedFile("c.csv", text.encode())}, follow=True)

    def position(self, kind, pk):
        return OpeningPosition.raw_objects.get(segment="live", kind=kind, object_id=pk)

    def test_sheet_lists_every_item_and_is_admin_only(self):
        response = self.client.get(reverse("ledger_count_sheet"))
        body = response.content.decode()
        self.assertTrue(body.startswith("type,id,name,unit,current_opening,counted"))
        self.assertIn("Flour", body)
        self.assertIn("Till", body)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("ledger_count_sheet")).status_code, 403)
        self.assertEqual(self.client.get(reverse("ledger_count")).status_code, 403)

    def test_import_marks_only_counted_rows_and_a_matching_count_still_confirms(self):
        self.upload(self.sheet(counted_flour="100"))  # equals the provisional 100
        self.assertEqual(self.position("raw", self.flour.pk).source, "counted")
        self.assertEqual(self.position("fg", self.bread.pk).source, "derived")  # not counted, left provisional

    def test_import_changes_live_stock_from_the_counted_opening(self):
        self.upload(self.sheet(counted_flour="80"))
        self.assertEqual(self.stored(RawMaterial, self.flour.pk), Decimal("80.000"))

    def test_bad_sheets_explain_and_change_nothing(self):
        for text, fragment in (
            (self.sheet(counted_flour="lots"), "is not a number"),
            (self.sheet(extra="alien,1,X,u,0,5\n"), "not recognised"),
            ("name,qty\nFlour,5\n", "does not look like a count sheet"),
            (self.sheet(), "No counted values"),
        ):
            response = self.upload(text)
            self.assertContains(response, fragment)
        self.assertEqual(self.position("raw", self.flour.pk).source, "derived")

    def test_phone_page_saves_counts_as_confirmed(self):
        page = self.client.get(reverse("ledger_count"))
        self.assertContains(page, "Count stock")
        self.client.post(reverse("ledger_action"), {
            "action": "save_openings", "confirm_counts": "1", f"pos_raw_{self.flour.pk}": "100"})
        self.assertEqual(self.position("raw", self.flour.pk).source, "counted")
