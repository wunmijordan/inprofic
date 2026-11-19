from django.shortcuts import render
from accounts.services import is_business_admin
from .models import RetailSettings
from .services import device_for_request


class DeviceAccessMiddleware:
    """Optional staff-device gate; registration and admin recovery remain reachable."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        business = getattr(request, "business", None)
        if (
            not business
            or not request.user.is_authenticated
            or is_business_admin(request.user, business)
        ):
            return self.get_response(request)
        from core.middleware import EXEMPT_PREFIXES

        if request.path.startswith(EXEMPT_PREFIXES) or request.path in {
            "/retail/profile/",
            "/retail/devices/register/",
        }:
            return self.get_response(request)
        device = device_for_request(request)
        required = RetailSettings.raw_objects.filter(
            business=business, require_approved_devices=True
        ).exists()
        if (device and device.status == "revoked") or (
            required and (not device or device.status != "approved")
        ):
            return render(request, "retail/device_access.html", status=403)
        return self.get_response(request)
