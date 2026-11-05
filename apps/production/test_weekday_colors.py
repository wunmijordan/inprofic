from datetime import date
from django.test import TestCase
from django.urls import reverse
from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business
from .forms import OrderForm, ProductionDaySettingForm
from .models import Order, ProductionDaySetting


class WeekdayColorTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Weekday bakery', slug='weekday-bakery')
        self.setting = ProductionDaySetting.raw_objects.create(
            business=self.business, weekday=0, color='#123456', expiry_days=3)

    def order_form(self, day, **kwargs):
        return OrderForm(data={'date': day, 'order_type': 'distribution', 'is_market_stock': 'on', **kwargs.pop('data', {})}, business=self.business, **kwargs)

    def test_same_weekday_across_dates_uses_color(self):
        for day in ['2026-11-02', '2026-11-09', '2027-01-04']:
            form = self.order_form(day)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.save(commit=False).color_tag, '#123456')
        form = self.order_form('2026-11-03')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).color_tag, '')

    def test_explicit_and_historical_order_tags_are_preserved(self):
        form = self.order_form('2026-11-02', data={'color_tag': '#abcdef'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).color_tag, '#abcdef')
        order = Order.raw_objects.create(business=self.business, date=date(2026,11,2), color_tag='#fedcba')
        form = self.order_form('2026-11-02', instance=order, data={'color_tag': '#fedcba'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).color_tag, '#fedcba')

    def test_other_business_rule_does_not_apply(self):
        other = Business.objects.create(name='Other', slug='other-weekday')
        ProductionDaySetting.raw_objects.create(business=other, weekday=1, color='#abcdef')
        form = self.order_form('2026-11-03')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).color_tag, '')

    def test_expiry_is_relative_to_production_date_including_zero_days(self):
        self.assertEqual(self.setting.suggested_expiry(date(2026,11,2)), date(2026,11,5))
        self.setting.expiry_days = 0
        self.assertEqual(self.setting.suggested_expiry(date(2026,11,9)), date(2026,11,9))
        self.setting.expiry_days = None
        self.assertIsNone(self.setting.suggested_expiry(date(2026,11,9)))

    def test_settings_require_valid_weekday_and_nonnegative_shelf_life(self):
        for data in [{'weekday':7, 'color':'#123456'}, {'weekday':0, 'color':'#123456', 'expiry_days':-1}]:
            self.assertFalse(ProductionDaySettingForm(data=data).is_valid())

    def test_settings_upsert_weekday_and_edit_collision_is_visible(self):
        roles = seed_business_roles(self.business)
        user = CustomUser.objects.create_user(username='weekday-admin',password='test-password')
        UserBusiness.objects.create(user=user,business=self.business,role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.client.force_login(user)
        url = reverse('production_settings')
        response = self.client.post(url, {'weekday':0,'color':'#abcdef','expiry_days':2})
        self.assertEqual(response.status_code,302)
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.color, '#abcdef')
        self.assertEqual(ProductionDaySetting.raw_objects.filter(business=self.business,weekday=0).count(),1)
        tuesday = ProductionDaySetting.raw_objects.create(business=self.business,weekday=1,color='#112233')
        response = self.client.post(f'{url}?edit={tuesday.pk}', {'weekday':0,'color':'#ffffff'})
        self.assertContains(response,'This weekday already has a color')
        tuesday.refresh_from_db()
        self.assertEqual(tuesday.weekday,1)
