from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse
from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business, FinancialTransaction
from core.services import tenant_backup_querysets
from inventory.models import FinishedGood, RawMaterial, RecipeItem, StockMovement
from sales.models import Customer, Sale
from .models import Order, OrderItem, OrderProposal, OrderProposalLine, ProposalOrderLink
from .test_kitchen import _formset


class ProductionProposalTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Proposal bakery', slug='proposal-bakery')
        self.user = CustomUser.objects.create_superuser(username='proposal-admin', password='test-password')
        self.client.force_login(self.user)
        self.customer = Customer.raw_objects.create(business=self.business, name='Ada')
        self.material = RawMaterial.raw_objects.create(business=self.business, name='Flour', stock=100, cost_per_unit=2)
        self.good = FinishedGood.raw_objects.create(business=self.business, name='Bread', unit='loaf', units_per_batch=10, stock=10, reorder_level=1)
        self.other_good = FinishedGood.raw_objects.create(business=self.business, name='Cake', unit='piece', units_per_batch=1, stock=10, reorder_level=1)
        for good in (self.good, self.other_good):
            RecipeItem.objects.create(finished_good=good, raw_material=self.material, qty_per_batch=1)
        self.proposal = OrderProposal.objects.create(business=self.business, title='Monday plan', planned_date=date(2026, 11, 9))
        self.a = OrderProposalLine.objects.create(proposal=self.proposal, finished_good=self.good, channel='distribution', customer=self.customer, quantity=20)
        self.b = OrderProposalLine.objects.create(proposal=self.proposal, finished_good=self.good, channel='physical_store', quantity=10)
        self.c = OrderProposalLine.objects.create(proposal=self.proposal, finished_good=self.other_good, channel='online', customer_name='Online buyer', quantity=5)

    def use(self, **overrides):
        values = {f'quantity-{line.pk}': str(line.quantity) for line in self.proposal.lines.all()}
        values.update(planned_date='2026-11-09', **overrides)
        return self.client.post(reverse('proposal_use', args=[self.proposal.pk]), values)

    def seed(self):
        response = self.use()
        self.assertEqual(response.status_code, 302)
        token = parse_qs(urlparse(response.url).query)['proposal_seed'][0]
        return token, self.client.get(response.url)

    def save_order(self, token):
        return self.client.post(reverse('order_add'), {'proposal_seed': token, 'date': '2026-11-09',
            'production_basis': 'product_quantity', **_formset([(self.good.pk, '0', '30'), (self.other_good.pk, '0', '5')])})

    def test_create_share_and_review_are_inert_and_snapshot_is_private(self):
        for name in ['proposal_list', 'proposal_add']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        response = self.client.get(reverse('proposal_detail', args=[self.proposal.pk]))
        self.assertContains(response, '20 loaves')
        self.assertContains(response, 'Share proposal')
        self.assertIn('Online buyer', response.context['proposal_share_text'])
        image = self.client.get(reverse('proposal_snapshot', args=[self.proposal.pk]))
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image['Content-Type'], 'image/png')
        self.assertEqual(image['Cache-Control'], 'private, no-store')
        self.seed()
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        self.material.refresh_from_db()
        self.assertEqual(self.material.stock, 100)

    def test_use_combines_products_and_prefills_actual_order_then_split(self):
        token, page = self.seed()
        self.assertEqual(page.status_code, 200)
        formset = page.context['formset']
        self.assertEqual(len(formset.forms), 2)
        self.assertEqual(formset.forms[0].initial['piece_qty'], '30.00')
        self.assertEqual(formset.forms[1].initial['piece_qty'], '5.00')
        response = self.save_order(token)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        order = Order.objects.get()
        self.assertEqual(order.order_type, 'unassigned')
        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.items.get(finished_good=self.good).total_units, 30)
        self.assertEqual(response.url, reverse('order_split', args=[order.pk]))
        page = self.client.get(response.url)
        self.assertEqual(page.status_code, 200)
        destinations = page.context['destinations']
        self.assertEqual([d['channel'] for d in destinations[:3]], ['distribution', 'physical_store', 'online'])
        self.assertEqual(destinations[0]['customer'], str(self.customer.pk))
        bread, cake = page.context['rows']
        self.assertEqual(bread['cells'][0]['batch'], '2')
        self.assertEqual(bread['cells'][1]['batch'], '1')
        self.assertEqual(cake['cells'][2]['batch'], '5')
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        self.assertFalse(Sale.objects.exists())
        # Retrying the exact submit returns the existing order.
        self.assertEqual(self.save_order(token).status_code, 302)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(ProposalOrderLink.objects.count(), 1)

    def test_quantities_can_be_adjusted_and_zero_omits_products(self):
        result = self.use(**{f'quantity-{self.a.pk}': '7.50', f'quantity-{self.c.pk}': '0'})
        page = self.client.get(result.url)
        self.assertEqual(page.context['formset'].forms[0].initial['piece_qty'], '17.50')
        self.assertEqual(len(page.context['formset'].forms), 1)
        bad = self.use(**{f'quantity-{self.a.pk}': '-1'})
        self.assertEqual(bad.status_code, 400)
        self.assertContains(bad, 'positive quantities', status_code=400)
        self.assertFalse(Order.objects.exists())

    def test_foreign_plan_and_products_are_rejected(self):
        foreign = Business.objects.create(name='Other proposal', slug='other-proposal')
        proposal = OrderProposal.objects.create(business=foreign, title='Private plan', planned_date=date(2026, 11, 9))
        self.assertEqual(self.client.get(reverse('proposal_detail', args=[proposal.pk])).status_code, 404)
        good = FinishedGood.raw_objects.create(business=foreign, name='Private cake', unit='piece')
        response = self.client.post(reverse('proposal_add'), {'title': 'Invalid', 'planned_date': '2026-11-09',
            'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0', 'lines-0-channel': 'physical_store',
            'lines-0-finished_good': good.pk, 'lines-0-quantity': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['formset'].errors[0]['finished_good'])

    def test_save_proposal_validates_and_still_has_no_domain_effects(self):
        response = self.client.post(reverse('proposal_add'), {'after_save':'share', 'title': 'Saved plan', 'planned_date': '2026-11-09',
            'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0', 'lines-0-channel': 'physical_store',
            'lines-0-finished_good': self.good.pk, 'lines-0-quantity': '12'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("?share=1"))
        self.assertEqual(OrderProposal.objects.get(title='Saved plan').lines.get().quantity, 12)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_archived_and_forged_seeds_cannot_be_used(self):
        self.client.post(reverse('proposal_archive', args=[self.proposal.pk]))
        self.assertEqual(self.use().status_code, 404)
        self.assertEqual(self.client.get(reverse('order_add')+'?proposal_seed=wrong').status_code, 404)
        self.assertEqual(self.client.get(reverse('proposal_use', args=[self.proposal.pk])).status_code, 405)

    def test_backup_registry_retains_proposals_and_links(self):
        models = {qs.model for qs in tenant_backup_querysets(self.business)}
        self.assertTrue({OrderProposal, OrderProposalLine, ProposalOrderLink}.issubset(models))

    def test_production_history_renders_requested_units_without_sale_only_properties(self):
        order = Order.objects.create(business=self.business, date=date(2026,11,9), order_type='physical_store')
        OrderItem.objects.create(order=order, finished_good=self.good, piece_qty=44)
        response = self.client.get(reverse('product_detail', args=[self.good.pk])+'?start=2026-11-09&end=2026-11-09')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Requested 44.00 loaves')

    def test_channel_split_prefill_is_accepted_and_preserves_customer_demand(self):
        token, _page = self.seed()
        response = self.save_order(token)
        page = self.client.get(response.url)
        post = {}
        for destination in page.context['destinations']:
            prefix = f"d{destination['index']}_"
            for key in ('channel', 'customer', 'customer_name', 'store_mode', 'purpose', 'pay_status', 'pay_method', 'pay_account'):
                post[prefix+key] = destination[key]
        for row in page.context['rows']:
            for cell in row['cells']:
                post[cell['batch_name']], post[cell['piece_name']] = cell['batch'], cell['piece']
        result = self.client.post(response.url, post)
        self.assertEqual(result.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.order_type, 'multi_channel')
        self.assertEqual(order.allocations.count(), 3)
        self.assertEqual(order.allocations.get(channel='distribution').customer, self.customer)
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        self.assertFalse(Sale.objects.exists())

    def test_backup_rewrites_proposal_snapshot_identifiers(self):
        from accounts.backup_restore import _remap_operational_references
        order = Order.objects.create(business=self.business, date=date(2026,11,9))
        link = ProposalOrderLink.objects.create(proposal=self.proposal, order=order, destination_key='combined', seed='old-seed',
            planned_destinations=[{'channel':'distribution', 'customer':999,
                'quantities':{'888':'20'}}])
        _remap_operational_references(self.business, {Customer:{999:self.customer.pk}, FinishedGood:{888:self.good.pk}})
        link.refresh_from_db()
        self.assertEqual(link.planned_destinations[0]['customer'], self.customer.pk)
        self.assertEqual(link.planned_destinations[0]['quantities'], {str(self.good.pk):'20'})
        self.assertNotEqual(link.seed, 'old-seed')

    def test_view_only_user_cannot_create_or_use_proposals(self):
        roles = seed_business_roles(self.business)
        from accounts.models import RoleModulePermission
        role = roles[CustomUser.ROLE_BUSINESS_ADMIN]
        # Use a regular custom role to avoid admin permission overrides.
        from accounts.models import Role
        role = Role.objects.create(business=self.business, key='proposal-viewer', name='Proposal viewer')
        RoleModulePermission.objects.create(role=role, module='production', can_view=True, can_edit=False)
        user = CustomUser.objects.create_user(username='proposal-viewer', password='test-password')
        UserBusiness.objects.create(user=user, business=self.business, role=role)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('proposal_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('proposal_add')).status_code, 403)
        self.assertEqual(self.use().status_code, 403)

    def test_combined_plan_keeps_products_without_physical_store_configuration(self):
        self.good.stock = None
        self.good.reorder_level = None
        self.good.save(update_fields=['stock', 'reorder_level'])
        token, page = self.seed()
        self.assertIn(self.good, page.context['formset'].forms[0].fields['finished_good'].queryset)
        self.assertEqual(page.context['formset'].forms[0]['finished_good'].value(), self.good.pk)

    def test_more_than_five_destinations_reach_the_existing_split_review(self):
        for index in range(4):
            customer = Customer.raw_objects.create(business=self.business, name=f'Additional customer {index}')
            OrderProposalLine.objects.create(proposal=self.proposal, channel='distribution', customer=customer,
                finished_good=self.good, quantity=1)
        token, page = self.seed()
        response = self.save_order(token)
        page = self.client.get(response.url)
        self.assertEqual(page.context['max_destinations'], 7)
        self.assertEqual(page.context['visible_destinations'], 7)
        self.assertEqual(len(ProposalOrderLink.objects.get().planned_destinations), 7)
