"""Returns credit the original customer without manufacturing or moving cash."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from core.accounting import _sale_expressions, profit_and_loss
from core.books import account_balance, rebuild_books
from core.finance_views import _finance_open_items
from core.models import Business, CashAccount, FinancialTransaction, JournalEntry, LedgerAccount
from sales.models import Customer, CustomerPayment, Sale, SaleItem
from .models import DistributionReturn, FinishedGood, FinishedGoodChannelPrice, MarketStockLot, MarketStockMovement
from .services import record_distribution_return, release_market_stock_items, sell_market_stock_items


class ReturnAccountingTests(TestCase):
    def setUp(self):
        self.day = timezone.localdate()
        self.business = Business.objects.create(name='Return accounting', slug='return-accounting')
        self.user = CustomUser.objects.create_superuser(username='return-admin', password='test-password')
        self.customer = Customer.raw_objects.create(business=self.business, name='Original customer')
        self.other = Customer.raw_objects.create(business=self.business, name='New customer')
        self.account = CashAccount.raw_objects.create(business=self.business, name='Till')
        self.good = FinishedGood.raw_objects.create(business=self.business, name='Loaf', unit='loaf', selling_price=100)
        FinishedGoodChannelPrice.objects.create(finished_good=self.good, channel="distribution", price=100)
        lot = MarketStockLot.raw_objects.create(business=self.business, finished_good=self.good,
            received_date=self.day, quantity_received=10, quantity_available=10, unit_cost=40)
        MarketStockMovement.raw_objects.create(business=self.business, lot=lot, date=self.day,
            movement_type=MarketStockMovement.PRODUCTION_IN, quantity=10, balance_after=10)
        self.sale = self.release(10)
        self.client.force_login(self.user)

    def release(self, qty, customer=None, paid=False):
        return release_market_stock_items(business=self.business,
            items=[{'finished_good': self.good, 'quantity': qty}], customer=customer or self.customer,
            date=self.day, payment_status='paid' if paid else 'unpaid', payment_method='Cash',
            account=self.account if paid else None, user=self.user)

    def returned(self, qty, condition=DistributionReturn.REDISTRIBUTABLE):
        return record_distribution_return(business=self.business, sale_item=self.sale.items.get(), quantity=qty,
            date=self.day, condition=condition, reason='Unsold goods returned', user=self.user)

    def pay(self, amount):
        return self.client.post(reverse('customer_payment_add'), {'sale': self.sale.pk,
            'customer': self.customer.name, 'date': self.day.isoformat(), 'amount': str(amount),
            'payment_method': 'Cash', 'account': self.account.pk})

    def test_return_credits_customer_and_finance_and_limits_payment(self):
        self.returned(3)
        self.assertEqual(self.sale.total, 1000)  # Original evidence retained.
        self.assertEqual(self.sale.net_total, 700)
        self.assertEqual(self.customer.outstanding_balance, 700)
        self.assertEqual(_finance_open_items(self.business)['receivables'], 700)
        self.assertEqual(self.pay(800).status_code, 200)
        self.assertFalse(CustomerPayment.raw_objects.filter(sale=self.sale).exists())
        self.assertEqual(self.pay(200).status_code, 302)
        self.assertEqual(self.customer.outstanding_balance, 500)
        self.assertEqual(self.pay(500).status_code, 302)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.transaction_type, 'paid')
        self.assertEqual(self.customer.outstanding_balance, 0)
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.business).aggregate(v=Sum('amount'))['v'], 700)
        pl = profit_and_loss(self.business, self.day, self.day)
        self.assertEqual((pl['revenue'], pl['cogs']), (700, 280))

    def test_resale_belongs_only_to_new_customer_and_store(self):
        self.returned(4)
        next_sale = self.release(2, self.other, paid=True)
        store_sale = sell_market_stock_items(business=self.business,
            items=[{'finished_good': self.good, 'quantity': 2}], date=self.day,
            payment_method='Cash', account=self.account, user=self.user)
        self.assertEqual(self.customer.outstanding_balance, 600)
        self.assertEqual(next_sale.customer_master, self.other)
        self.assertEqual(store_sale.source, 'walkin')
        self.assertEqual(profit_and_loss(self.business, self.day, self.day)['revenue'], 400)
        self.assertEqual(_finance_open_items(self.business)['receivables'], 600)

    def test_damage_is_loss_at_cost_not_customer_debt_and_books_balance(self):
        self.returned(2, DistributionReturn.DAMAGED)
        self.assertEqual(self.customer.outstanding_balance, 800)
        pl = profit_and_loss(self.business, self.day, self.day)
        self.assertEqual(pl['write_offs'], 80)
        self.assertFalse(FinancialTransaction.raw_objects.filter(business=self.business).exists())
        rebuild_books(self.business)
        balance = lambda key: account_balance(self.business,
            LedgerAccount.raw_objects.get(business=self.business, system_key=key))
        self.assertEqual(balance('ar'), 800)
        self.assertEqual(balance('cogs'), 320)
        self.assertEqual(balance('adjustments'), 80)
        for entry in JournalEntry.raw_objects.filter(business=self.business):
            self.assertEqual(sum(line.debit for line in entry.lines.all()), sum(line.credit for line in entry.lines.all()))
        self.assertEqual(JournalEntry.raw_objects.filter(source_type='distribution_return').count(), 1)

    def test_existing_returns_reduce_balances_without_reposting_cash(self):
        self.assertEqual(self.pay(600).status_code, 302)
        DistributionReturn.raw_objects.create(business=self.business, sale_item=self.sale.items.get(),
            date=self.day, quantity=4, condition=DistributionReturn.REDISTRIBUTABLE, unit_value=40)
        self.assertEqual(self.customer.outstanding_balance, 0)
        self.assertEqual(_finance_open_items(self.business)['receivables'], 0)
        self.assertEqual(profit_and_loss(self.business, self.day, self.day)['revenue'], 600)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=self.sale).amount, 600)

    def test_paid_return_keeps_actual_receipt_and_exposes_credit_in_books(self):
        self.assertEqual(self.pay(1000).status_code, 302)
        self.returned(3)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=self.sale).amount, 1000)
        self.assertEqual(FinancialTransaction.raw_objects.get(business=self.business).amount, 1000)
        rebuild_books(self.business)
        self.assertEqual(account_balance(self.business,
            LedgerAccount.raw_objects.get(business=self.business, system_key='ar')), -300)

    def test_basic_dashboard_counts_paid_and_partial_distribution_with_store_sales(self):
        self.assertEqual(self.pay(200).status_code, 302)
        self.returned(2)
        self.release(1, self.other, paid=True)
        sell_market_stock_items(business=self.business, items=[{'finished_good': self.good, 'quantity': 1}],
            date=self.day, payment_method='Cash', account=self.account, user=self.user)
        response = self.client.get(reverse('dashboard') + '?view=simple')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['today_revenue'], 400)
        self.assertEqual(response.context['today_sales_count'], 3)

    def test_return_credit_respects_commercial_price_and_multiple_returns_in_sql(self):
        item = self.sale.items.get()
        item.commercial_quantity = 2
        item.commercial_unit_price = Decimal('107.50')
        item.save()
        self.returned(2)
        self.returned(1, DistributionReturn.DAMAGED)
        money, line, cost, units = _sale_expressions(prefix='')
        totals = SaleItem.objects.filter(pk=item.pk).aggregate(value=Sum(line), cost=Sum(cost), units=Sum(units))
        self.assertEqual(totals['value'], Decimal('150.50'))
        self.assertEqual(totals['cost'], 280)
        self.assertEqual(totals['units'], 7)
        self.assertEqual(self.sale.net_total, Decimal('150.50'))

    def test_foreign_returns_do_not_credit_tenant_or_enter_finance(self):
        other_business = Business.objects.create(name='Private returns', slug='private-returns')
        # Corrupt foreign data cannot credit another business's invoice.
        DistributionReturn.raw_objects.create(business=other_business, sale_item=self.sale.items.get(),
            date=self.day, quantity=3, condition=DistributionReturn.DAMAGED, unit_value=40)
        self.assertEqual(self.sale.net_total, 1000)
        self.assertEqual(_finance_open_items(self.business)['receivables'], 1000)
        self.assertEqual(profit_and_loss(self.business, self.day, self.day)['write_offs'], 0)

    def test_repricing_after_return_settlement_does_not_invent_a_payment(self):
        from sales.models import CustomerProductPrice
        from sales.repricing import apply_price_review, price_review
        self.assertEqual(self.pay(600).status_code, 302)
        self.returned(4)
        CustomerProductPrice.raw_objects.create(business=self.business, customer=self.customer,
            finished_good=self.good, channel='distribution', price=80)
        review = price_review(business=self.business, customer=self.customer, start=self.day, end=self.day, user=self.user)
        self.assertEqual((review['rows'][0]['paid'], review['credit_total']), (600, 120))
        apply_price_review(business=self.business, customer=self.customer, start=self.day, end=self.day,
            user=self.user, fingerprint=review['fingerprint'])
        self.assertEqual(CustomerPayment.raw_objects.get(sale=self.sale).amount, 600)
        self.assertEqual(FinancialTransaction.raw_objects.get(business=self.business).amount, 600)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.net_total, 480)
        self.assertEqual(self.customer.outstanding_balance, 0)

    def test_backlog_balances_include_later_live_returns_and_payments(self):
        from core.context import set_current_business, set_ledger_context
        DistributionReturn.raw_objects.create(business=self.business, sale_item=self.sale.items.get(),
            date=self.day + timedelta(days=1), quantity=4, condition=DistributionReturn.REDISTRIBUTABLE, unit_value=40)
        CustomerPayment.raw_objects.create(business=self.business, sale=self.sale, customer=self.customer.name,
            date=self.day + timedelta(days=1), amount=600, account=self.account)
        set_current_business(self.business)
        set_ledger_context('backlog', self.day + timedelta(days=1), 'active')
        try:
            self.assertEqual(self.customer.outstanding_balance, 0)
            self.assertEqual(_finance_open_items(self.business)['receivables'], 0)
            self.assertEqual(profit_and_loss(self.business, self.day, self.day)['revenue'], 600)
        finally:
            set_current_business(None)
            set_ledger_context()
