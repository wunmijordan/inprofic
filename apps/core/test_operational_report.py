from datetime import date
from decimal import Decimal

from django.test import TestCase

from commerce.models import CommerceIntake
from core.models import Business
from core.operational_report import collect_operational_report, render_operational_pdf
from inventory.models import FinishedGood, MarketStockLot, MarketStockMovement, RawMaterial
from procurement.models import PurchaseOrder, PurchaseOrderItem
from sales.models import Sale, SaleItem


class OperationalReportTests(TestCase):
    def test_monthly_report_attributes_paid_sales_once_and_stays_in_business(self):
        business = Business.objects.create(name="Report Bakery", slug="report-bakery")
        other = Business.objects.create(name="Other Bakery", slug="other-report-bakery")
        product = FinishedGood.objects.create(business=business, name="Cake", unit="piece")
        other_product = FinishedGood.objects.create(business=other, name="Bread", unit="piece")
        report_date = date(2026, 9, 25)

        external_sale = Sale.objects.create(business=business, date=report_date, transaction_type="paid")
        external_item = SaleItem.objects.create(sale=external_sale, finished_good=product, piece_qty=1, price=Decimal("100"), unit_cost=Decimal("40"))
        CommerceIntake.objects.create(
            business=business, source=CommerceIntake.SOURCE_API,
            ordering_mode=CommerceIntake.MODE_STOCK, customer_name="Customer",
            accepted_sale=external_sale,
        )
        manual_sale = Sale.objects.create(business=business, date=report_date, transaction_type="paid")
        SaleItem.objects.create(sale=manual_sale, finished_good=product, piece_qty=1, price=Decimal("50"), unit_cost=Decimal("15"))
        other_sale = Sale.objects.create(business=other, date=report_date, transaction_type="paid")
        SaleItem.objects.create(sale=other_sale, finished_good=other_product, piece_qty=1, price=Decimal("900"))

        material = RawMaterial.objects.create(
            business=business, name="Flour", stock=Decimal("10"),
            cost_per_unit=Decimal("2"), reorder_level=Decimal("5"),
        )
        purchase = PurchaseOrder.objects.create(
            business=business, date=report_date, received_date=report_date, status="received",
        )
        PurchaseOrderItem.objects.create(purchase_order=purchase, raw_material=material, qty=2, unit_cost=Decimal("10"))
        other_material = RawMaterial.objects.create(business=other, name="Sugar")
        other_purchase = PurchaseOrder.objects.create(
            business=other, date=report_date, received_date=report_date, status="received",
        )
        PurchaseOrderItem.objects.create(purchase_order=other_purchase, raw_material=other_material, qty=10, unit_cost=Decimal("20"))
        lot = MarketStockLot.objects.create(
            business=business, finished_good=product, source=MarketStockLot.SOURCE_RETURN,
            source_sale_item=external_item, received_date=report_date,
            quantity_received=2, quantity_available=1, unit_cost=Decimal("7"),
        )
        MarketStockMovement.objects.create(
            business=business, lot=lot, date=report_date, movement_type=MarketStockMovement.RETURN_IN,
            quantity=2, balance_after=2, unit_value=Decimal("7"),
        )
        MarketStockMovement.objects.create(
            business=business, lot=lot, date=report_date, movement_type=MarketStockMovement.RELEASE,
            quantity=-1, balance_after=1, unit_value=Decimal("7"),
        )

        report = collect_operational_report(business, date(2026, 8, 1), date(2026, 9, 30))
        august, september = report["rows"]
        self.assertEqual(august["revenue"], Decimal("0"))
        self.assertEqual(september["revenue"], Decimal("150"))
        self.assertEqual(september["external"], Decimal("100"))
        self.assertEqual(september["manual"], Decimal("50"))
        self.assertEqual(september["cogs"], Decimal("55"))
        self.assertEqual(september["procurement"], Decimal("20"))
        self.assertEqual(report["snapshot"]["raw_value"], Decimal("20"))
        self.assertEqual(september["market_in"], Decimal("14"))
        self.assertEqual(september["market_out"], Decimal("7"))
        self.assertEqual(report["snapshot"]["market_value"], Decimal("7"))
        response = render_operational_pdf(business, report)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))
