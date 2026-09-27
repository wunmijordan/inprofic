import json

from django.contrib import messages
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone

from accounts.services import is_business_admin, user_has_permission

from .alert_services import acknowledge_inventory_alerts, alert_settings, inventory_alert_feed
from .forms import InventoryAlertSettingsForm


def _feed_generation_key(business_id):
    return f"inventory-alert-feed-generation:{business_id}"


def _feed_generation(business_id):
    return cache.get(_feed_generation_key(business_id), "0") or "0"


def _feed_cache_key(business_id, user_id):
    return f"inventory-alert-feed:v2:{business_id}:{user_id}:{_feed_generation(business_id)}"


def _invalidate_business_feed_cache(business_id):
    # A generation token avoids wildcard deletes while invalidating every
    # user's cached view of the same business alert settings.
    cache.set(_feed_generation_key(business_id), str(timezone.now().timestamp()), timeout=None)


def _invalidate_user_feed_cache(business_id, user_id):
    cache.delete(_feed_cache_key(business_id, user_id))


@login_required
@require_GET
def alert_feed(request):
    if not user_has_permission(request.user, request.business, "inventory", "view"):
        return JsonResponse({"detail": "Inventory access is required."}, status=403)
    cache_key = _feed_cache_key(request.business.pk, request.user.pk)
    payload = cache.get(cache_key)
    if payload is None:
        payload = inventory_alert_feed(business=request.business, user=request.user)
        # The tray polls frequently. A short cache cuts repeated remote-DB
        # round trips while keeping stock changes visible within one polling
        # cycle; acknowledgement invalidates this user's entry immediately.
        cache.set(cache_key, payload, timeout=20)
    return JsonResponse(payload)


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
    _invalidate_user_feed_cache(request.business.pk, request.user.pk)
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
        _invalidate_business_feed_cache(request.business.pk)
        messages.success(request, "Inventory alert settings saved.")
        return redirect("inventory_alert_settings")
    return render(request, "inventory/alert_settings.html", {"form": form})
