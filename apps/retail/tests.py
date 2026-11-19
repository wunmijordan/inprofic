from decimal import Decimal
from django.test import TestCase, Client
from django.utils import timezone
from django.core.exceptions import ValidationError
from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business
from inventory.models import FinishedGood, MarketStockLot, StockMovement
from inventory.services import market_stock_available, record_finished_good_movement
from commerce.models import StorefrontProduct, CommerceCheckoutSession, CommerceIntake
from commerce.checkout_services import create_checkout, available_physical_stock
from .models import (
    BranchStock,
    StockTransfer,
    ProductPromotion,
    CustomerQuotation,
    QuotationLine,
    OfflineEntry,
)
from .services import (
    main_branch,
    create_branch,
    dispatch_transfer,
    receive_transfer,
    branch_stock,
)


class BranchIntegrityTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Branch business", slug="branch", vertical="retail"
        )
        self.other = Business.objects.create(
            name="Other", slug="other", vertical="retail"
        )
        self.user = CustomUser.objects.create_user(
            username="owner", fullname="Owner", password="pw"
        )
        roles = seed_business_roles(self.business)
        UserBusiness.objects.create(
            user=self.user, business=self.business, role=roles["business_admin"]
        )
        self.main = main_branch(self.business)
        self.branch = create_branch(
            self.business, self.user, name="Ikeja", code="ikeja"
        )
        self.good = FinishedGood.raw_objects.create(
            business=self.business,
            name="Product",
            unit="piece",
            stock=100,
            selling_price=100,
            source_type="purchased_for_resale",
        )
        self.product = StorefrontProduct.raw_objects.create(
            business=self.business, finished_good=self.good
        )

    def transfer(self, quantity=30):
        return dispatch_transfer(
            business=self.business,
            user=self.user,
            source=self.main,
            destination=self.branch,
            product=self.good,
            quantity=quantity,
        )

    def test_transit_and_receipt_preserve_global_and_main_allocations(self):
        transfer = self.transfer()
        self.assertEqual(branch_stock(self.good, self.main), 70)
        self.assertEqual(branch_stock(self.good, self.branch), 0)
        receive_transfer(transfer, self.user, 30)
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 100)
        self.assertEqual(branch_stock(self.good, self.branch), 30)
        with self.assertRaises(ValidationError):
            receive_transfer(transfer, self.user, 30)

    def test_receiving_shortage_is_audited_global_writeoff(self):
        transfer = self.transfer()
        receive_transfer(transfer, self.user, 25, "5 damaged in transit")
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 95)
        self.assertEqual(branch_stock(self.good, self.main), 70)
        self.assertEqual(branch_stock(self.good, self.branch), 25)

    def test_main_movements_cannot_consume_branch_allocations(self):
        receive_transfer(self.transfer(), self.user, 30)
        with self.assertRaises(ValidationError):
            record_finished_good_movement(self.good, -80, StockMovement.FG_SALE)
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 100)

    def test_other_business_branch_is_rejected(self):
        other = main_branch(self.other)
        with self.assertRaises(ValidationError):
            dispatch_transfer(
                business=self.business,
                user=self.user,
                source=self.main,
                destination=other,
                product=self.good,
                quantity=1,
            )

    def test_expiry_is_advisory_and_blank_dates_are_allowed(self):
        from datetime import timedelta

        MarketStockLot.raw_objects.create(
            business=self.business,
            finished_good=self.good,
            received_date=timezone.localdate() - timedelta(days=2),
            expiry_date=timezone.localdate() - timedelta(days=1),
            quantity_received=10,
            quantity_available=10,
        )
        self.assertEqual(market_stock_available(self.good, timezone.localdate()), 10)

    def test_quotations_have_no_stock_or_cash_effect(self):
        quote = CustomerQuotation.raw_objects.create(
            business=self.business,
            customer_name="Customer",
            valid_until=timezone.localdate(),
        )
        QuotationLine.objects.create(
            quotation=quote, product=self.product, quantity=2, unit_price=90
        )
        self.assertEqual(quote.total, 180)
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 100)
        self.assertFalse(CommerceCheckoutSession.raw_objects.exists())

    def test_quotation_total_matches_rounded_checkout_lines(self):
        quote = CustomerQuotation.raw_objects.create(
            business=self.business,
            customer_name="Fractional quantity",
            valid_until=timezone.localdate(),
        )
        for _ in range(2):
            QuotationLine.objects.create(
                quotation=quote,
                product=self.product,
                quantity=Decimal(".15"),
                unit_price=Decimal(".10"),
            )
        self.assertEqual(quote.total, Decimal(".04"))

    def test_quotation_channel_prices_and_adjustments_are_recorded_internally(self):
        from inventory.models import FinishedGoodChannelPrice
        from core.models import AuditLog

        FinishedGoodChannelPrice.objects.create(
            finished_good=self.good, channel="online", price=120
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        response = self.client.get("/retail/quotations/add/")
        self.assertEqual(
            response.context["quotation_prices"][str(self.product.pk)]["online"],
            "120.00",
        )
        response = self.client.post(
            "/retail/quotations/add/",
            {
                "title": "Supply request",
                "customer_name": "Agreed price buyer",
                "valid_until": timezone.localdate().isoformat(),
                "channel": "online",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-product": str(self.product.pk),
                "lines-0-quantity": "2",
                "lines-0-unit_price": "110",
                "lines-0-channel_unit_price": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        quote = CustomerQuotation.raw_objects.get(customer_name="Agreed price buyer")
        line = quote.lines.get()
        self.assertEqual(line.channel_unit_price, Decimal("120"))
        self.assertEqual(line.unit_price, Decimal("110"))
        log = AuditLog.objects.get(
            business=self.business, action="quotation_create", object_id=str(quote.pk)
        )
        self.assertEqual(
            log.metadata["price_adjustments"][0]["channel_price"], "120.00"
        )
        self.assertEqual(log.created_by, self.user)
        FinishedGoodChannelPrice.objects.filter(
            finished_good=self.good, channel="online"
        ).update(price=130)
        line.refresh_from_db()
        self.assertEqual(line.channel_unit_price, Decimal("120"))
        staff = self.client.get(f"/retail/quotations/{quote.pk}/")
        self.assertContains(staff, "Adjusted from channel price")
        public = self.client.get(f"/shared/quotations/{quote.public_id}/")
        self.assertNotContains(public, "Internal price record")
        self.assertEqual(quote.total, Decimal("220"))
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 100)

    def test_offline_sync_requires_confirmation(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        response = self.client.post(
            "/retail/offline/sync/",
            data={"entries": []},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(OfflineEntry.raw_objects.exists())

    def test_profile_and_branch_workspace_render(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        for path in [
            "/retail/",
            "/retail/profile/",
            "/retail/devices/",
            "/retail/expiration/",
            "/retail/quotations/",
            "/retail/promotions/",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_readonly_offline_packages_hide_write_controls_and_reject_replay(self):
        import hashlib
        import uuid
        from accounts.models import Role, RoleModulePermission, BusinessModuleAccess
        from .models import RegisteredDevice

        role = Role.objects.create(business=self.business, name="Read only offline")
        for module in ("inventory", "delivery", "dashboard", "sales"):
            RoleModulePermission.objects.create(
                role=role, module=module, can_view=True, can_edit=False
            )
            BusinessModuleAccess.objects.update_or_create(
                business=self.business, module=module, defaults={"enabled": True}
            )
        user = CustomUser.objects.create_user(
            username="offline-reader", fullname="Reader"
        )
        UserBusiness.objects.create(user=user, business=self.business, role=role)
        token = "read-only-test-device"
        RegisteredDevice.raw_objects.create(
            business=self.business,
            user=user,
            name="Reader device",
            status="approved",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        self.client.force_login(user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session[f"retail_device_token:{self.business.pk}:{user.pk}"] = token
        session.save()
        response = self.client.get("/retail/offline/snapshot/")
        self.assertEqual(response.status_code, 200)
        snapshot = response.json()
        self.assertTrue(snapshot["packages"]["inventory"])
        self.assertFalse(snapshot["packages"]["inventory_edit"])
        self.assertFalse(snapshot["packages"]["delivery_edit"])
        self.assertFalse(snapshot["packages"]["sales"])
        response = self.client.post(
            "/retail/offline/sync/",
            data={
                "confirmed": True,
                "owner": snapshot["owner"],
                "entries": [
                    {
                        "id": str(uuid.uuid4()),
                        "kind": "stock_note",
                        "data": {"note": "Not allowed"},
                    }
                ],
            },
            content_type="application/json",
        )
        self.assertFalse(response.json()["results"][0]["ok"])
        self.assertFalse(OfflineEntry.raw_objects.exists())


from commerce.test_payments import CommercePaymentTestBase


class RetailCheckoutTests(CommercePaymentTestBase):
    def checkout(self, key="retail", qty=3, branch=None):
        return create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_STAFF_POS,
            customer={"name": "Retail Customer"},
            items=[{"storefront_product": self.product, "quantity": qty}],
            order_mode="physical_store",
            idempotency_key=key,
            branch=branch,
        )[0]

    def test_branch_holds_do_not_reduce_other_branch_availability(self):
        from accounts.services import seed_business_roles

        user = CustomUser.objects.create_user(username="branch-owner", fullname="Owner")
        roles = seed_business_roles(self.business)
        UserBusiness.objects.create(
            user=user, business=self.business, role=roles["business_admin"]
        )
        main = main_branch(self.business)
        branch = create_branch(self.business, user, name="Branch", code="b")
        transfer = dispatch_transfer(
            business=self.business,
            user=user,
            source=main,
            destination=branch,
            product=self.good,
            quantity=5,
        )
        receive_transfer(transfer, user, 5)
        checkout = self.checkout(branch=branch)
        self.assertEqual(available_physical_stock(self.good, branch=branch), 2)
        self.assertEqual(available_physical_stock(self.good), 15)

    def test_promotion_total_matches_line_total_without_changing_master_price(self):
        promo = ProductPromotion.raw_objects.create(
            business=self.business,
            name="Buy two get one",
            mode="buy_get",
            qualifying_quantity=2,
            free_quantity=1,
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
        )
        promo.products.add(self.good)
        checkout = self.checkout()
        self.assertEqual(checkout.amount, Decimal("7500.00"))
        self.assertEqual(
            sum(row.line_total for row in checkout.items.all()), checkout.amount
        )
        self.good.refresh_from_db()
        self.assertEqual(self.good.selling_price, Decimal("2500"))

    def test_mixed_cash_and_verified_transfer_posts_once(self):
        from commerce.models import CommerceDirectTransferRoute, CommercePaymentReceipt
        from core.models import FinancialTransaction
        from .payments import settle_split_checkout

        self.config.transfer_enabled = True
        self.config.save(update_fields=["transfer_enabled"])
        route = CommerceDirectTransferRoute.raw_objects.create(
            business=self.business,
            name="Transfer",
            bank_name="Bank",
            account_name="Store",
            account_number="0000000000",
            transfer_account=self.settlement_account,
        )
        user = CustomUser.objects.create_user(username="cashier", fullname="Cashier")
        checkout = self.checkout(qty=2)
        receipt = settle_split_checkout(checkout, user, "2000", route.pk)
        checkout.refresh_from_db()
        self.good.refresh_from_db()
        self.assertIsNotNone(checkout.materialized_intake_id)
        self.assertEqual(self.good.stock, 18)
        receipts = CommercePaymentReceipt.raw_objects.filter(business=self.business)
        self.assertEqual(receipts.count(), 2)
        self.assertEqual(sum(r.amount for r in receipts), Decimal("5000"))

    def test_confirmed_offline_cash_replay_is_idempotent(self):
        import uuid
        from core.models import FinancialTransaction
        from accounts.services import seed_business_roles

        user = CustomUser.objects.create_user(
            username="offline-owner", fullname="Owner"
        )
        roles = seed_business_roles(self.business)
        UserBusiness.objects.create(
            user=user, business=self.business, role=roles["business_admin"]
        )
        self.client.force_login(user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        snapshot = self.client.get("/retail/offline/snapshot/").json()
        entry = {
            "id": str(uuid.uuid4()),
            "kind": "cash_sale",
            "data": {
                "customer": "Offline buyer",
                "items": [
                    {
                        "id": str(self.product.public_id),
                        "quantity": "2",
                        "price": "2500",
                    }
                ],
            },
        }
        payload = {"confirmed": True, "owner": snapshot["owner"], "entries": [entry]}
        first = self.client.post(
            "/retail/offline/sync/", data=payload, content_type="application/json"
        )
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["results"][0]["ok"], first.json())
        second = self.client.post(
            "/retail/offline/sync/", data=payload, content_type="application/json"
        )
        self.assertEqual(
            second.json()["results"][0]["result"], first.json()["results"][0]["result"]
        )
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 18)
        self.assertEqual(
            FinancialTransaction.raw_objects.filter(business=self.business).count(), 1
        )

    def test_offline_price_conflict_stays_unposted(self):
        import uuid
        from accounts.services import seed_business_roles

        user = CustomUser.objects.create_user(
            username="offline-conflict", fullname="Owner"
        )
        roles = seed_business_roles(self.business)
        UserBusiness.objects.create(
            user=user, business=self.business, role=roles["business_admin"]
        )
        self.client.force_login(user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        snapshot = self.client.get("/retail/offline/snapshot/").json()
        payload = {
            "confirmed": True,
            "owner": snapshot["owner"],
            "entries": [
                {
                    "id": str(uuid.uuid4()),
                    "kind": "cash_sale",
                    "data": {
                        "items": [
                            {
                                "id": str(self.product.public_id),
                                "quantity": "2",
                                "price": "2000",
                            }
                        ]
                    },
                }
            ],
        }
        response = self.client.post(
            "/retail/offline/sync/", data=payload, content_type="application/json"
        )
        self.assertFalse(response.json()["results"][0]["ok"])
        self.assertFalse(OfflineEntry.raw_objects.exists())
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 20)

    def offline_login(self):
        user = CustomUser.objects.create_user(
            username="offline-scope", fullname="Owner"
        )
        roles = seed_business_roles(self.business)
        UserBusiness.objects.create(
            user=user, business=self.business, role=roles["business_admin"]
        )
        from accounts.models import BusinessModuleAccess

        for module in ("dashboard", "sales", "finance", "delivery"):
            BusinessModuleAccess.objects.update_or_create(
                business=self.business, module=module, defaults={"enabled": True}
            )
        self.client.force_login(user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        return user

    def delivery_fixture(self):
        from commerce.models import DeliveryAssignment, DeliveryDriver, DeliveryOrigin

        origin = DeliveryOrigin.raw_objects.create(
            business=self.business, name="Store", address="Lagos"
        )
        driver = DeliveryDriver.raw_objects.create(
            business=self.business, name="Prepared rider", provider="inhouse"
        )
        assignment = DeliveryAssignment.raw_objects.create(
            business=self.business,
            intake=self.intake,
            origin=origin,
            provider="inhouse",
        )
        return driver, assignment

    def test_offline_snapshot_contains_dashboard_and_business_dispatch_package(self):
        self.offline_login()
        driver, assignment = self.delivery_fixture()
        snapshot = self.client.get("/retail/offline/snapshot/").json()
        self.assertTrue(snapshot["packages"]["dashboard"])
        self.assertTrue(snapshot["packages"]["delivery"])
        self.assertTrue(snapshot["packages"]["sales"])
        self.assertEqual(set(snapshot["dashboard"]), {"sales", "revenue", "profit"})
        self.assertEqual(snapshot["deliveries"][0]["id"], assignment.pk)
        self.assertEqual(snapshot["drivers"][0]["id"], driver.pk)
        self.assertEqual(snapshot["products"][0]["stock"], "20.00")
        # Preparing packages never creates a sale or dispatch assignment.
        assignment.refresh_from_db()
        self.assertIsNone(assignment.driver_id)

    def test_offline_dispatch_confirms_once_and_rejects_stale_order(self):
        import uuid

        self.offline_login()
        driver, assignment = self.delivery_fixture()
        snapshot = self.client.get("/retail/offline/snapshot/").json()
        entry = {
            "id": str(uuid.uuid4()),
            "kind": "dispatch_assignment",
            "data": {
                "assignment": assignment.pk,
                "driver": driver.pk,
                "updated_at": assignment.updated_at.isoformat(),
            },
        }
        payload = {"confirmed": True, "owner": snapshot["owner"], "entries": [entry]}
        first = self.client.post(
            "/retail/offline/sync/", data=payload, content_type="application/json"
        ).json()
        self.assertTrue(first["results"][0]["ok"], first)
        assignment.refresh_from_db()
        self.assertEqual(assignment.driver_id, driver.pk)
        self.assertEqual(assignment.status, "assigned")
        replay = self.client.post(
            "/retail/offline/sync/", data=payload, content_type="application/json"
        ).json()
        self.assertEqual(first, replay)
        entry["id"] = str(uuid.uuid4())
        stale = self.client.post(
            "/retail/offline/sync/", data=payload, content_type="application/json"
        ).json()
        self.assertFalse(stale["results"][0]["ok"])
        self.assertIn("changed after preparation", stale["results"][0]["error"])

    def test_pos_review_uses_promotions_and_leaves_no_reservation_or_payment(self):
        self.offline_login()
        promo = ProductPromotion.raw_objects.create(
            business=self.business,
            name="Buy two get one",
            mode="buy_get",
            qualifying_quantity=2,
            free_quantity=1,
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
        )
        promo.products.add(self.good)
        from django.urls import reverse
        from commerce.models import CommercePayment

        response = self.client.post(
            reverse("commerce_storefront_pos"),
            {
                "preview": "1",
                "method": "cash",
                "order_mode": "physical_store",
                f"qty_{self.product.public_id}": "3",
                "customer_name": "Preview buyer",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], "7500.00")
        self.assertFalse(CommerceCheckoutSession.raw_objects.exists())
        self.assertFalse(CommercePayment.raw_objects.exists())
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 20)

    def test_paid_secondary_branch_sale_preserves_main_stock_and_offline_date(self):
        from datetime import timedelta
        from commerce.payment_services import initiate_payment, record_verified_payment
        from sales.models import Sale

        user = self.offline_login()
        main = main_branch(self.business)
        branch = create_branch(self.business, user, name="Secondary", code="secondary")
        transfer = dispatch_transfer(
            business=self.business,
            user=user,
            source=main,
            destination=branch,
            product=self.good,
            quantity=5,
        )
        receive_transfer(transfer, user, 5)
        recorded_at = timezone.now() - timedelta(days=1)
        checkout, _ = create_checkout(
            business=self.business,
            source="staff_pos",
            order_mode="physical_store",
            customer={"name": "Backdated branch buyer"},
            items=[{"storefront_product": self.product, "quantity": "2"}],
            branch=branch,
            recorded_at=recorded_at,
            idempotency_key="secondary-payment",
        )
        payment = initiate_payment(
            checkout=checkout,
            method="cash",
            surface="pos",
            idempotency_key="secondary-payment",
        )
        record_verified_payment(
            payment=payment,
            amount=checkout.amount,
            actor=user,
            verified_at=recorded_at,
            idempotency_key="secondary-receipt",
        )
        checkout.refresh_from_db()
        self.assertEqual(
            checkout.status, "materialized", checkout.materialization_error
        )
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 18)
        self.assertEqual(branch_stock(self.good, branch), 3)
        self.assertEqual(branch_stock(self.good, main), 15)
        self.assertEqual(
            Sale.raw_objects.get(
                business=self.business, customer="Backdated branch buyer"
            ).date,
            timezone.localtime(recorded_at).date(),
        )

    def test_separate_reward_product_is_free_and_both_products_are_released_once(self):
        from commerce.payment_services import initiate_payment, record_verified_payment

        user = self.offline_login()
        reward = FinishedGood.raw_objects.create(
            business=self.business,
            name="Reward product",
            unit="unit",
            stock=10,
            reorder_level=1,
            selling_price=1000,
        )
        product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=reward,
            published=True,
            allow_stock_order=True,
        )
        promotion = ProductPromotion.raw_objects.create(
            business=self.business,
            name="Buy two get a different product",
            mode="buy_get",
            qualifying_quantity=2,
            free_quantity=1,
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
        )
        promotion.products.add(self.good)
        promotion.reward_products.add(reward)
        checkout, _ = create_checkout(
            business=self.business,
            source="staff_pos",
            customer={"name": "Reward buyer"},
            items=[
                {"storefront_product": self.product, "quantity": 2},
                {"storefront_product": product, "quantity": 1},
            ],
            order_mode="physical_store",
            idempotency_key="different-reward",
        )
        self.assertEqual(checkout.amount, Decimal("6000"))
        self.assertEqual(checkout.items.get(finished_good=reward, is_promotion_reward=True).unit_price, 0)
        self.assertEqual(checkout.items.get(finished_good=reward, is_promotion_reward=False).unit_price, 1000)
        payment = initiate_payment(
            checkout=checkout,
            method="cash",
            surface="pos",
            idempotency_key="reward-cash",
        )
        record_verified_payment(
            payment=payment,
            amount=checkout.amount,
            actor=user,
            idempotency_key="reward-receipt",
        )
        checkout.refresh_from_db()
        self.assertEqual(
            checkout.status, "materialized", checkout.materialization_error
        )
        self.good.refresh_from_db()
        reward.refresh_from_db()
        self.assertEqual(checkout.materialized_intake.items.filter(is_promotion_reward=True).count(), 1)
        self.assertEqual(checkout.materialized_intake.items.get(is_promotion_reward=True).unit_price, 0)
        self.assertEqual(self.good.stock, 18)
        self.assertEqual(reward.stock, 8)

    def test_shared_quotation_document_uses_business_details_without_payment_confirmation(
        self,
    ):
        self.offline_login()
        self.business.contact_phone = "08000000000"
        self.business.contact_address = "Shop address"
        self.business.save(update_fields=["contact_phone", "contact_address"])
        quote = CustomerQuotation.raw_objects.create(
            business=self.business,
            customer_name="Quote buyer",
            valid_until=timezone.localdate(),
            status="sent",
        )
        QuotationLine.objects.create(
            quotation=quote, product=self.product, quantity=2, unit_price=2500
        )
        response = self.client.get(f"/shared/quotations/{quote.public_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "receipt-card")
        self.assertContains(response, "Shop address")
        self.assertContains(response, "08000000000")
        self.assertContains(response, "Customer quotation")
        self.assertNotContains(response, "Payment confirmed")
        self.assertFalse(CommerceCheckoutSession.raw_objects.exists())
