import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.services import is_business_admin, user_has_permission

from .alert_services import acknowledge_inventory_alerts, alert_settings, inventory_alert_feed
from .forms import InventoryAlertSettingsForm


@login_required
@require_GET
def alert_feed(request):
    if not user_has_permission(request.user, request.business, "inventory", "view"):
        return JsonResponse({"detail": "Inventory access is required."}, status=403)
    return JsonResponse(inventory_alert_feed(business=request.business, user=request.user))


@login_required
@require_POST
def alert_acknowledge(request):
    if not user_has_permission(request.user, request.business, "inventory", "view"):
        return JsonResponse({"detail": "Inventory access is required."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid alert request."}, status=400)
    ids = payload.get("alert_ids") or []
    if not isinstance(ids, list):
        return JsonResponse({"detail": "alert_ids must be a list."}, status=400)
    updated = acknowledge_inventory_alerts(business=request.business, user=request.user, alert_ids=ids)
    return JsonResponse({"acknowledged": updated})


@login_required
def alert_settings_view(request):
    if not (is_business_admin(request.user, request.business) or user_has_permission(request.user, request.business, "inventory", "edit")):
        return render(request, "403.html", status=403)
    instance = alert_settings(request.business)
    form = InventoryAlertSettingsForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.business = request.business
        saved.created_by = saved.created_by or request.user
        saved.save()
        messages.success(request, "Inventory alert settings saved.")
        return redirect("inventory_alert_settings")
    return render(request, "inventory/alert_settings.html", {"form": form})
