from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from accounts.models import (
    CustomUser,
    UserBusiness,
    BusinessModuleAccess,
    SubscriptionService,
)
from accounts.services import (
    seed_business_roles,
    accessible_businesses,
    user_has_permission,
)
from core.models import Business
from core.vocabulary import labels
from core.verticals import vertical_config
from inventory.models import FinishedGood, RawMaterial, RecipeItem, StockMovement
from .models import UserProfile, Branch
from .forms import ProfileForm, QuotationForm
from .services import create_branch, main_branch, dispatch_transfer, receive_transfer
from .catalogue import synchronize_catalogue


class BranchWorkspaceTests(TestCase):
    def setUp(self):
        self.hq = Business.objects.create(name="HQ", slug="hq", vertical="bakery")
        self.other = Business.objects.create(
            name="Other", slug="other", vertical="retail"
        )
        roles = seed_business_roles(self.hq)
        self.owner = CustomUser.objects.create_user(username="owner", fullname="Owner")
        self.manager = CustomUser.objects.create_user(
            username="manager", fullname="Manager"
        )
        UserBusiness.objects.create(
            user=self.owner, business=self.hq, role=roles["business_admin"]
        )
        UserBusiness.objects.create(
            user=self.manager, business=self.hq, role=roles["stock_keeper"]
        )
        self.material = RawMaterial.raw_objects.create(
            business=self.hq, name="Flour", stock=200, usage_unit="kg"
        )
        self.product = FinishedGood.raw_objects.create(
            business=self.hq,
            name="Loaf",
            unit="loaf",
            stock=50,
            selling_price=100,
            reorder_level=1,
        )
        RecipeItem.objects.create(
            finished_good=self.product, raw_material=self.material, qty_per_batch=2
        )

    def branch(self, mode="shared"):
        return create_branch(
            self.hq,
            self.owner,
            manager=self.manager,
            name="Island",
            code="island",
            catalogue_mode=mode,
        )

    def test_shared_catalogue_copies_definitions_not_balances(self):
        branch = self.branch()
        target = FinishedGood.raw_objects.get(business=branch.workspace)
        self.assertEqual(target.stock, 0)
        self.assertEqual(
            target.recipe_items.get().raw_material.business_id, branch.workspace_id
        )
        target.stock = 7
        target.save(update_fields=["stock"])
        self.product.selling_price = 150
        self.product.save()
        synchronize_catalogue(branch)
        target.refresh_from_db()
        self.assertEqual(target.stock, 7)
        self.assertEqual(target.selling_price, 150)
        self.assertFalse(
            SubscriptionService.objects.filter(business=branch.workspace).exists()
        )

    def test_headquarters_access_and_revocation_are_dynamic(self):
        branch = self.branch()
        self.assertIn(branch.workspace, accessible_businesses(self.owner))
        self.assertTrue(
            user_has_permission(self.owner, branch.workspace, "inventory", "edit")
        )
        self.assertTrue(
            user_has_permission(self.manager, branch.workspace, "inventory", "edit")
        )
        self.assertNotIn(self.other, accessible_businesses(self.manager))
        UserBusiness.objects.filter(user=self.owner, business=self.hq).update(
            active=False
        )
        self.assertNotIn(branch.workspace, accessible_businesses(self.owner))
        self.assertFalse(
            user_has_permission(self.owner, branch.workspace, "inventory", "view")
        )

    def test_independent_catalogue_and_parent_entitlement_ceiling(self):
        branch = self.branch("independent")
        self.assertFalse(
            FinishedGood.raw_objects.filter(business=branch.workspace).exists()
        )
        BusinessModuleAccess.objects.create(
            business=self.hq, module="sales", enabled=False
        )
        self.assertFalse(
            user_has_permission(self.manager, branch.workspace, "sales", "edit")
        )
        self.assertEqual(branch.workspace.vertical, self.hq.vertical)

    def test_foreign_manager_is_rejected_atomically(self):
        stranger = CustomUser.objects.create_user(username="stranger")
        with self.assertRaises(ValidationError):
            create_branch(self.hq, self.owner, manager=stranger, name="Bad", code="bad")
        self.assertFalse(Branch.raw_objects.filter(code="bad").exists())

    def test_shared_catalogue_post_is_blocked_and_workspace_switch_works(self):
        branch = self.branch()
        self.client.force_login(self.owner)
        response = self.client.post(
            "/business/switch/", {"business_id": branch.workspace_id}
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post("/inventory/product/add/", {"name": "Bypass"})
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/retail/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.business.pk, branch.workspace_id)

    def test_transfer_receipt_moves_stock_to_branch_once(self):
        branch = self.branch()
        transfer = dispatch_transfer(
            business=self.hq,
            user=self.owner,
            source=main_branch(self.hq),
            destination=branch,
            product=self.product,
            quantity=10,
        )
        receive_transfer(transfer, self.manager, 9, note="One damaged in transit")
        self.product.refresh_from_db()
        target = FinishedGood.raw_objects.get(business=branch.workspace)
        self.assertEqual(self.product.stock, 40)
        self.assertEqual(target.stock, 9)
        self.assertTrue(
            StockMovement.raw_objects.filter(
                business=branch.workspace,
                finished_good=target,
                quantity=9,
                affects_stock=True,
            ).exists()
        )
        with self.assertRaises(ValidationError):
            receive_transfer(transfer, self.manager, 9)

    def test_profile_picture_can_be_cleared_without_upload(self):
        profile = UserProfile.objects.create(
            user=self.owner, photo="staff-profiles/old.png"
        )
        form = ProfileForm(
            {"fullname": "Owner", "remove_photo": "on"},
            user=self.owner,
            instance=profile,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        profile.refresh_from_db()
        self.assertFalse(profile.photo)

    def test_quotation_requires_a_user_title(self):
        data = {
            "customer_name": "Buyer",
            "channel": "online",
            "valid_until": timezone.localdate(),
        }
        form = QuotationForm(data)
        self.assertIn("title", form.errors)
        form = QuotationForm({**data, "title": "November supplies"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_vertical_names_are_scoped_and_do_not_change_capability(self):
        self.hq.label_overrides = {
            "bakery": {"inventory": "Stores", "orders": "Bake requests"},
            "retail": {"inventory": "Shop stock"},
        }
        self.hq.save()
        self.assertEqual(labels(self.hq)["inventory"], "Stores")
        self.assertEqual(vertical_config(self.hq)["orders"], "Bake requests")
        self.assertEqual(labels(self.other)["inventory"], "Inventory")
        self.hq.vertical = "retail"
        self.assertEqual(labels(self.hq)["inventory"], "Shop stock")
        self.assertFalse(vertical_config(self.hq)["uses_production"])

    def test_catalogue_measurement_conflict_does_not_block_switch_to_headquarters(self):
        branch = self.branch()
        self.product.unit = "box"
        self.product.save(update_fields=["unit"])
        self.client.force_login(self.owner)
        session = self.client.session
        session["active_business_id"] = branch.workspace_id
        session.save()
        self.assertEqual(self.client.get("/retail/").status_code, 409)
        response = self.client.post("/business/switch/", {"business_id": self.hq.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["active_business_id"], self.hq.pk)
        target = FinishedGood.raw_objects.get(business=branch.workspace)
        self.assertEqual(target.unit, "loaf")

    def test_each_vertical_retains_its_production_capability_in_branches(self):
        from accounts.services import business_has_module

        for index, vertical in enumerate(
            ["bakery", "restaurant", "general", "wholesale", "retail"]
        ):
            with self.subTest(vertical=vertical):
                self.hq.vertical = vertical
                self.hq.save(update_fields=["vertical"])
                branch = create_branch(
                    self.hq,
                    self.owner,
                    manager=self.manager,
                    name=f"Location {index}",
                    code=f"location-{index}",
                    catalogue_mode="independent",
                )
                self.assertEqual(branch.workspace.vertical, vertical)
                self.assertEqual(
                    business_has_module(branch.workspace, "production"),
                    vertical not in {"wholesale", "retail"},
                )
