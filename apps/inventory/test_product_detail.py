from datetime import date
from django.test import TestCase
from django.urls import reverse
from accounts.models import CustomUser
from core.models import Business
from inventory.models import FinishedGood, StockMovement
from sales.models import Customer, CustomerProductPrice, Sale, SaleItem


class ProductDetailTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Product analysis', slug='product-analysis')
        self.user = CustomUser.objects.create_superuser(username='product-admin', password='test-password')
        self.good = FinishedGood.raw_objects.create(business=self.business, name='Bread', unit='loaf', units_per_batch=1)
        self.customer = Customer.raw_objects.create(business=self.business, name='Ada')
        CustomerProductPrice.raw_objects.create(business=self.business, customer=self.customer, finished_good=self.good, channel='distribution', price=80)
        self.client.force_login(self.user)
        self.url = reverse('product_detail', args=[self.good.pk])

    def sale(self, day, price, cost):
        sale = Sale.raw_objects.create(business=self.business, date=day, source='distribution_order', customer_master=self.customer, customer='Ada')
        SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=10, price=price, unit_cost=cost)

    def test_product_period_analysis_uses_saved_cost_and_prices_with_global_units(self):
        self.sale(date(2026, 11, 1), 80, 40)
        self.sale(date(2026, 10, 31), 100, 70)
        response = self.client.get(self.url, {'start':'2026-11-01', 'end':'2026-11-01'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['realized']['revenue'], 800)
        self.assertEqual(response.context['realized']['cost'], 400)
        self.assertEqual(response.context['realized']['profit'], 400)
        self.assertEqual(response.context['realized']['margin'], 50)
        self.assertContains(response, '10.00 loaves')
        self.assertContains(response, 'Ada')
        self.assertContains(response, 'Stock history and workflow', html=False)

    def test_invalid_dates_and_other_tenant_are_safe(self):
        response = self.client.get(self.url, {'start':'2026-11-02', 'end':'2026-11-01'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'end date must be on or after')
        foreign = Business.objects.create(name='Private', slug='private-product-analysis')
        good = FinishedGood.raw_objects.create(business=foreign, name='Private product')
        self.assertEqual(self.client.get(reverse('product_detail', args=[good.pk])).status_code, 404)

    def test_inventory_links_to_shared_detail(self):
        response = self.client.get(reverse('inventory'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.url)
