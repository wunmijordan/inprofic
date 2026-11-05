"""Start a local UI fixture server with a disposable schema and seeded records."""
import os
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix='inprofic-ui-') as directory:
    os.environ['INPROFIC_UI_DB'] = str(Path(directory) / 'fixture.sqlite3')
    os.environ['DJANGO_SETTINGS_MODULE'] = 'scripts.ui.settings'
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', run_syncdb=True, verbosity=0, interactive=False)
    from accounts.models import CustomUser, UserBusiness
    from accounts.services import seed_business_roles
    from accounts.subscription_services import ensure_default_plans
    from core.models import Business, CashAccount
    from core.onboarding import CURRENT_TOUR_VERSION
    from inventory.models import RawMaterial, FinishedGood, RecipeItem
    from sales.models import Customer
    business = Business.objects.create(name='UI Fixture Bakery', slug='ui-fixture', assistant_enabled=True, background_color='#050733')
    roles = seed_business_roles(business)
    admin = CustomUser.objects.create_user(username='ui-admin', password='ui-fixture-only', fullname='UI Admin')
    viewer = CustomUser.objects.create_user(username='ui-viewer', password='ui-fixture-only', fullname='UI Viewer')
    UserBusiness.objects.create(user=admin, business=business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN], onboarding_tour_version=CURRENT_TOUR_VERSION)
    # Explicit read-only permission overrides keep the fixture independent of
    # the set of built-in operational roles.
    from accounts.models import Role, RoleModulePermission
    readonly = Role.objects.create(business=business, name='UI read only')
    for module,_ in RoleModulePermission.MODULE_CHOICES:
        RoleModulePermission.objects.create(role=readonly,module=module,can_view=True,can_edit=False)
    UserBusiness.objects.create(user=viewer,business=business,role=readonly,onboarding_tour_version=CURRENT_TOUR_VERSION)
    flour = RawMaterial.raw_objects.create(business=business,name='Fixture Flour',stock=100,reorder_level=10,purchase_unit='bag',package_qty=50,package_unit='kg',usage_unit='kg',cost_per_unit=100)
    RawMaterial.raw_objects.create(business=business,name='Low Flour',stock=5,reorder_level=10,purchase_unit='kg',package_unit='kg',usage_unit='kg')
    RawMaterial.raw_objects.create(business=business,name='Warning Sugar',stock=12,reorder_level=10,purchase_unit='kg',package_unit='kg',usage_unit='kg')
    good = FinishedGood.raw_objects.create(business=business,name='Fixture Loaf',unit='loaf',units_per_batch=110,selling_price=250)
    RecipeItem.objects.create(finished_good=good,raw_material=flour,qty_per_batch=20)
    for index in range(12): Customer.raw_objects.create(business=business,name=f'Customer {index:02}', region='Lagos' if index == 0 else '', customer_group='Distributors' if index == 0 else '')
    CashAccount.raw_objects.create(business=business,name='Fixture Till',opening_balance=1000)
    # Reproducible customer allocations and outstanding invoices for sharing/payment tests.
    from django.utils import timezone
    from sales.models import Sale, SaleItem
    from inventory.models import MarketStockLot, MarketStockMovement
    day = timezone.localdate()
    lot = MarketStockLot.objects.create(business=business, finished_good=good, received_date=day,
                                       quantity_received=100, quantity_available=60)
    MarketStockMovement.objects.create(business=business, lot=lot, date=day,
        movement_type=MarketStockMovement.PRODUCTION_IN, quantity=100, balance_after=100)
    remaining = 100
    for index, quantity in enumerate([10, 15, 15]):
        sale = Sale.objects.create(business=business, date=day, customer=f'Customer {index:02}',
                                   source='distribution_order', transaction_type='unpaid')
        SaleItem.objects.create(sale=sale, finished_good=good, piece_qty=quantity, price=250)
        remaining -= quantity
        MarketStockMovement.objects.create(business=business, lot=lot, date=day, sale=sale,
            movement_type=MarketStockMovement.RELEASE, quantity=-quantity, balance_after=remaining)
    outsider = Business.objects.create(name='Other UI Tenant',slug='other-ui')
    CashAccount.raw_objects.create(business=outsider,name='PRIVATE OTHER TENANT',opening_balance=500)
    ensure_default_plans()
    port = os.environ.get('INPROFIC_UI_PORT', '8765')
    print(f'UI fixture ready on http://127.0.0.1:{port}', flush=True)
    call_command('runserver', f'127.0.0.1:{port}', use_reloader=False, verbosity=0)
