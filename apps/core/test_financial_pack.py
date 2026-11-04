"""Financial pack and its revocable share links."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from inventory.models import FinishedGood, RawMaterial
from sales.models import Sale, SaleItem

from . import financial_pack as pack
from .models import Business, CashAccount, FinancialPackShare
from .test_ledger import BACKLOG_DAY, CUTOVER, LIVE_DAY, LedgerBase

TODAY = timezone.localdate()
MONTH_START = TODAY.replace(day=1)


class PackBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Pack Foods", slug="pack-foods")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="pack-admin", password="pw-123456")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        other_key = next(k for k in roles if k not in {CustomUser.ROLE_BUSINESS_ADMIN, "manager", "md_director"})
        self.staff = CustomUser.objects.create_user(username="pack-staff", password="pw-123456")
        UserBusiness.objects.create(user=self.staff, business=self.business, role=roles[other_key])
        CashAccount.raw_objects.create(business=self.business, name="Till", opening_balance=Decimal("1000"))
        RawMaterial.raw_objects.create(business=self.business, name="Flour", usage_unit="kg", stock=Decimal("10"), cost_per_unit=Decimal("5"))
        self.bread = FinishedGood.raw_objects.create(business=self.business, name="Bread", unit="loaf", stock=Decimal("4"))


class PositionTests(PackBase):
    def test_position_is_assets_minus_liabilities(self):
        data = pack.build_pack(self.business, MONTH_START, TODAY)
        self.assertEqual(data["cash_total"], Decimal("1000"))
        self.assertEqual(data["inventory"]["raw"], Decimal("50"))
        position = data["position"]
        self.assertEqual(position["assets"], data["cash_total"] + data["receivables"] + data["inventory"]["total"])
        self.assertEqual(position["net"], position["assets"] - position["liabilities"])

    def test_ageing_buckets_cover_every_open_invoice(self):
        data = pack.build_pack(self.business, MONTH_START, TODAY)
        self.assertEqual(len(data["receivable_aging"]), 4)
        self.assertEqual(sum(b["total"] for b in data["receivable_aging"]), data["receivables"])


class InternalViewTests(PackBase):
    def test_admin_sees_the_pack_and_the_honesty_note(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("finance_pack"))
        self.assertContains(page, "Statement of financial position")
        self.assertContains(page, "not a double-entry general ledger")
        self.assertContains(page, "Pack Foods")

    def test_only_business_admins_manage_share_links(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("finance_pack_shares")).status_code, 403)
        self.assertEqual(self.client.post(reverse("finance_pack_shares"), {}).status_code, 403)


class ShareLinkTests(PackBase):
    def create(self, **extra):
        self.client.force_login(self.admin)
        payload = {"start": MONTH_START.isoformat(), "end": TODAY.isoformat(), "days": "7", "label": "Bank review", **extra}
        response = self.client.post(reverse("finance_pack_shares"), payload)
        link = response.context["new_link"]
        self.client.logout()
        return link, link.rstrip("/").rsplit("/", 1)[-1]

    def test_link_opens_anonymously_read_only_and_uncached(self):
        link, token = self.create()
        response = self.client.get(link)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pack Foods")
        self.assertContains(response, "Read-only view")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("noindex", response["X-Robots-Tag"])
        share = FinancialPackShare.raw_objects.get()
        self.assertEqual(share.view_count, 1)
        self.assertNotIn(token, share.token_hash)  # only a hash is stored

    def test_unknown_expired_and_revoked_links_all_look_identical(self):
        link, token = self.create()
        self.assertEqual(self.client.get(reverse("shared_pack", args=["not-a-real-token"])).status_code, 404)
        FinancialPackShare.raw_objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.get(link).status_code, 404)
        FinancialPackShare.raw_objects.update(expires_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.client.get(link).status_code, 200)
        self.client.force_login(self.admin)
        share = FinancialPackShare.raw_objects.get()
        self.client.post(reverse("finance_pack_revoke", args=[share.pk]))
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 404)

    def test_the_link_shows_its_own_business_even_to_a_logged_in_user_of_another(self):
        link, _ = self.create()
        other = Business.objects.create(name="Someone Else Ltd", slug="someone-else")
        roles = seed_business_roles(other)
        visitor = CustomUser.objects.create_user(username="visitor", password="pw-123456")
        UserBusiness.objects.create(user=visitor, business=other, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.client.force_login(visitor)
        page = self.client.get(link)
        self.assertContains(page, "Pack Foods")
        self.assertNotContains(page, "Someone Else Ltd")

    def test_expiry_is_capped_at_thirty_days(self):
        self.create(days="9999")
        share = FinancialPackShare.raw_objects.get()
        self.assertLessEqual(share.expires_at, timezone.now() + timedelta(days=30, minutes=1))


class ShareScopeTests(LedgerBase):
    def test_a_share_excludes_the_backlog_unless_asked_to_include_it(self):
        self.start_cutover()
        for day, name in ((BACKLOG_DAY, "Old"), (LIVE_DAY, "New")):
            sale = Sale.raw_objects.create(business=self.business, date=day, customer=name, transaction_type="paid", source="walkin")
            SaleItem.objects.create(sale=sale, finished_good=self.bread, piece_qty=Decimal("1"), price=Decimal("100"), unit_cost=Decimal("10"))
        start, end = CUTOVER - timedelta(days=10), TODAY
        live_share, live_token = pack.create_share(self.business, self.admin, start, end)
        all_share, all_token = pack.create_share(self.business, self.admin, start, end, include_backlog=True)
        self.client.logout()
        live = self.client.get(reverse("shared_pack", args=[live_token])).context["p"]
        combined = self.client.get(reverse("shared_pack", args=[all_token])).context["p"]
        self.assertEqual(live["pnl"]["revenue"], Decimal("100"))
        self.assertEqual(combined["pnl"]["revenue"], Decimal("200"))
