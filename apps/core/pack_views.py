"""Financial pack: internal view, and revocable read-only share links."""
from datetime import date as date_cls

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import financial_pack as pack
from . import ledger
from .accounting import fiscal_period
from .context import set_current_business, set_ledger_context
from .models import FinancialPackShare
from .services import audit


def _parse(raw, default):
    try:
        return date_cls.fromisoformat((raw or "").strip()) if raw else default
    except ValueError:
        return default


def _period(request, business):
    period = fiscal_period(business)
    start = _parse(request.GET.get("start"), period["start"])
    end = _parse(request.GET.get("end"), min(period["end"], timezone.localdate()))
    return (end, start) if start > end else (start, end)


@login_required
@require_GET
def finance_pack(request):
    start, end = _period(request, request.business)
    data = pack.build_pack(request.business, start, end)
    return render(request, "core/financial_pack.html", {"p": data, "shared": False, "can_share": ledger.can_manage(request.user, request.business)})


@login_required
@require_http_methods(["GET", "POST"])
def finance_pack_shares(request):
    business = request.business
    if not ledger.can_manage(request.user, business):
        return render(request, "403.html", status=403)
    new_link = None
    if request.method == "POST":
        start, end = _parse(request.POST.get("start"), None), _parse(request.POST.get("end"), None)
        if not start or not end or start > end:
            messages.error(request, "Choose a valid period for the pack.")
        else:
            try:
                days = int(request.POST.get("days") or 7)
            except ValueError:
                days = 7
            share, token = pack.create_share(
                business, request.user, start, end, days=days,
                include_backlog=request.POST.get("include_backlog") == "1", label=request.POST.get("label", ""),
            )
            audit(business, request.user, "pack_share_create", share,
                  f"Financial pack link created for {start} to {end}, expires {share.expires_at:%d %b %Y}",
                  {"include_backlog": share.include_backlog})
            new_link = request.build_absolute_uri(f"/shared/pack/{token}/")
    shares = FinancialPackShare.objects.filter(business=business)[:30]
    period = fiscal_period(business)
    return render(request, "core/pack_shares.html", {
        "shares": shares, "new_link": new_link, "default_start": period["start"],
        "default_end": min(period["end"], timezone.localdate()),
    })


@login_required
@require_POST
def finance_pack_revoke(request, pk):
    if not ledger.can_manage(request.user, request.business):
        return render(request, "403.html", status=403)
    share = get_object_or_404(FinancialPackShare.objects, pk=pk, business=request.business)
    pack.revoke_share(share)
    audit(request.business, request.user, "pack_share_revoke", share, "Financial pack link revoked")
    messages.success(request, "Link revoked. It no longer opens.")
    return redirect("finance_pack_shares")


@require_GET
def shared_pack(request, token):
    """Public, read-only. Unknown, expired and revoked links all look identical."""
    share = pack.resolve_share(token)
    if share is None:
        raise Http404()
    business = share.business
    # Anonymous visitors have no business context, so the share supplies it.
    set_current_business(business)
    cutover = ledger.latest_cutover(business)
    if cutover and cutover.status not in ("reverted", "merged"):
        set_ledger_context("all" if share.include_backlog else "live", cutover.cutover_date, cutover.status)
    data = pack.build_pack(business, share.period_start, share.period_end)
    FinancialPackShare.raw_objects.filter(pk=share.pk).update(
        view_count=share.view_count + 1, last_viewed_at=timezone.now())
    response = render(request, "core/financial_pack.html", {"p": data, "shared": True, "share": share})
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
