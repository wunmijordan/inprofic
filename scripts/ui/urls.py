"""Test-only routes. Production never imports this URL configuration."""
from django.contrib.auth import login
from django.shortcuts import redirect
from django.urls import path
from accounts.models import CustomUser
from storetrack.urls import urlpatterns as application_urls


def sign_in(request, role):
    user = CustomUser.objects.get(username=f'ui-{role}')
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    request.session['active_business_id'] = user.business_memberships.first().business_id
    return redirect('dashboard')

urlpatterns = [path('accounts/login/ui-fixture/<str:role>/', sign_in), *application_urls]
