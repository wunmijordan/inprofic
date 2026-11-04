"""Discoverability and access for Brief, Books and Tax through the real workspace."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BusinessModuleAccess, CustomUser, Role, RoleModulePermission, UserBusiness
from accounts.services import seed_business_roles

from .models import Business, DailyBriefPreference, LedgerCutover


class WorkspaceNavigationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Navigation Bakery", slug="navigation-bakery")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="navigation-admin", password="pw")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])

    def viewer(self, *, finance=False):
        role = Role.objects.create(business=self.business, key="navigation_viewer", name="Viewer")
        for module in ("dashboard", "finance"):
            RoleModulePermission.objects.create(role=role, module=module, can_view=module == "dashboard" or finance)
        user = CustomUser.objects.create_user(username="navigation-viewer", password="pw", email="viewer@example.com")
        UserBusiness.objects.create(user=user, business=self.business, role=role)
        self.client.force_login(user)
        return user

    def test_finance_has_tools_and_both_sidebars_have_daily_brief(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("finance_dashboard"))
        self.assertEqual(response.status_code, 200)
        # Both desktop and mobile share the same navigation partial.
        self.assertContains(response, f'href="{reverse("daily_brief")}"', count=2)
        self.assertContains(response, 'aria-label="Finance tools"')
        for name in ("books_home", "tax_home", "einvoices"):
            self.assertContains(response, f'href="{reverse(name)}"')

    def test_dashboard_viewer_can_save_own_brief_without_finance_access(self):
        user = self.viewer()
        response = self.client.get(reverse("daily_brief"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{reverse("books_home")}"')
        self.assertNotContains(response, f'href="{reverse("tax_home")}"')
        response = self.client.post(reverse("daily_brief"), {"action": "email", "send_email": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DailyBriefPreference.raw_objects.get(business=self.business, user=user).send_email)
        self.assertEqual(self.client.post(reverse("daily_brief"), {"action": "template"}).status_code, 403)
        for name in ("books_home", "tax_home"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_finance_viewer_sees_tools_without_write_controls(self):
        self.viewer(finance=True)
        response = self.client.get(reverse("books_home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '>Build books</button>')
        self.assertContains(response, 'aria-current="page">Books</a>')
        self.assertEqual(self.client.post(reverse("books_home")).status_code, 403)
        response = self.client.get(reverse("input_vat"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="vat_amount"')
        self.assertEqual(self.client.post(reverse("input_vat"), {}).status_code, 403)

    def test_finance_entitlement_hides_and_blocks_tools_for_admin(self):
        self.client.force_login(self.admin)
        BusinessModuleAccess.objects.update_or_create(business=self.business, module="finance", defaults={"enabled": False})
        response = self.client.get(reverse("daily_brief"))
        self.assertEqual(response.status_code, 200)
        for name in ("books_home", "tax_home", "einvoices"):
            self.assertNotContains(response, f'href="{reverse(name)}"')
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_ledger_status_container_is_admin_only_in_every_scope(self):
        LedgerCutover.raw_objects.create(business=self.business, cutover_date=timezone.localdate())
        viewer = self.viewer()
        membership = UserBusiness.objects.get(user=viewer, business=self.business)
        RoleModulePermission.objects.create(role=membership.role, module="ledger", can_view=True, can_edit=True)
        for user, is_admin in ((self.admin, True), (viewer, False)):
            self.client.force_login(user)
            for scope in ("live", "backlog", "all"):
                with self.subTest(user=user.username, scope=scope):
                    session = self.client.session
                    session["ledger_mode"] = scope
                    session.save()
                    response = self.client.get(reverse("dashboard"), {"view": "simple"})
                    self.assertEqual(response.status_code, 200)
                    if is_admin:
                        self.assertContains(response, "data-ledger-status")
                    else:
                        self.assertNotContains(response, "data-ledger-status")
                        self.assertNotContains(response, 'class="ledger-period-marquee"')
                    if scope in {"backlog", "all"}:
                        self.assertContains(response, "Return to Live")

    def test_complete_dashboard_has_no_ledger_selector(self):
        LedgerCutover.raw_objects.create(business=self.business, cutover_date=timezone.localdate())
        self.client.force_login(self.admin)
        response = self.client.get(reverse("complete_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ledger-scope-select")
        self.assertNotContains(response, "Ledger setup")
        self.assertContains(response, 'id="complete-dashboard-group"')
