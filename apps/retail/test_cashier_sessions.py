from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from commerce.test_payments import CommercePaymentTestBase
from commerce.models import CommerceIntake, CommerceCheckoutSession
from commerce.checkout_services import create_checkout
from commerce.payment_services import (
    initiate_payment,
    record_verified_payment,
    reverse_payment_receipt,
)
from .models import CashierSession, CashierCheckout, RetailSettings
from .services import (
    main_branch,
    create_branch,
    close_session,
    cashier_summary,
    pos_context,
)


class CashierSessionTests(CommercePaymentTestBase):
    def setUp(self):
        super().setUp()
        self.user = CustomUser.objects.create_user(
            username="drawer-owner", fullname="Owner"
        )
        roles = seed_business_roles(self.business)
        UserBusiness.objects.create(
            user=self.user, business=self.business, role=roles["business_admin"]
        )
        self.branch = main_branch(self.business)
        self.drawer = CashierSession.raw_objects.create(
            business=self.business,
            branch=self.branch,
            user=self.user,
            opening_float=100,
        )

    def checkout(self):
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_STAFF_POS,
            customer={"name": "Cash customer"},
            items=[{"storefront_product": self.product, "quantity": 2}],
            idempotency_key="drawer-checkout",
            order_mode="physical_store",
            branch=self.branch,
        )
        CashierCheckout.objects.create(session=self.drawer, checkout=checkout)
        return checkout

    def test_pending_payment_blocks_close_and_cash_receipt_adds_to_float(self):
        checkout = self.checkout()
        with self.assertRaises(ValidationError):
            close_session(self.drawer, self.user, 100, "")
        payment = initiate_payment(
            checkout=checkout,
            method="cash",
            surface="pos",
            idempotency_key="drawer-payment",
        )
        record_verified_payment(
            payment=payment,
            amount=checkout.amount,
            actor=self.user,
            idempotency_key="drawer-receipt",
        )
        summary = cashier_summary(self.drawer)
        self.assertEqual(summary["expected_cash"], Decimal("5100"))
        self.assertEqual(summary["pending_count"], 0)
        with self.assertRaises(ValidationError):
            close_session(self.drawer, self.user, 5090, "")
        close_session(self.drawer, self.user, 5090, "Cash short by 10")
        self.drawer.refresh_from_db()
        self.assertEqual(self.drawer.variance, -10)
        self.assertEqual(self.drawer.expected_cash, 5100)

    def test_drawer_detail_and_review_are_business_scoped(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/retail/cashier/{self.drawer.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opening float")
        close_session(self.drawer, self.user, 100, "")
        response = self.client.post(f"/retail/cashier/{self.drawer.pk}/review/")
        self.assertEqual(response.status_code, 302)
        self.drawer.refresh_from_db()
        self.assertEqual(self.drawer.reviewed_by, self.user)
        reviewed_at = self.drawer.reviewed_at
        self.client.post(f"/retail/cashier/{self.drawer.pk}/review/")
        self.drawer.refresh_from_db()
        self.assertEqual(self.drawer.reviewed_at, reviewed_at)
        from core.models import Business

        foreign = Business.objects.create(name="Private", slug="private-drawer")
        stranger = CustomUser.objects.create_user(username="stranger")
        roles = seed_business_roles(foreign)
        UserBusiness.objects.create(
            user=stranger, business=foreign, role=roles["business_admin"]
        )
        self.client.force_login(stranger)
        response = self.client.get(f"/retail/cashier/{self.drawer.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_session_in_another_location_blocks_untracked_sales(self):
        other = create_branch(
            self.business, self.user, name="Other location", code="other"
        )
        request = RequestFactory().post("/commerce/storefront-pos/")
        request.business = self.business
        request.user = self.user
        request.session = {f"branch:{self.business.pk}": other.pk}
        with self.assertRaisesMessage(ValidationError, "original branch"):
            pos_context(request)
