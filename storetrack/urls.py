from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import include, path
from accounts import views as account_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("users/", include("accounts.urls")),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/signup/", account_views.signup, name="signup"),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("", include("core.urls")),
    path("inventory/", include("inventory.urls")),
    path("procurement/", include("procurement.urls")),
    path("", include("production.urls")),
    path("sales/", include("sales.urls")),
    path("expenses/", include("expenses.urls")),
    path("", include("commerce.urls")),
    path("", include("whatsapp_storefront.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
