import json
import re

from django.conf import settings

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.templatetags.static import static
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.utils.cache import patch_vary_headers

from accounts.models import UserBusiness
from .models import Business


def _pwa_build_version():
    raw = str(getattr(settings, "PWA_BUILD_VERSION", "current") or "current")
    return re.sub(r"[^A-Za-z0-9._-]", "-", raw)[:40] or "current"


def _icons():
    return [
        {
            "src": static("core/pwa/icon-192.png"),
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": static("core/pwa/icon-512.png"),
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": static("core/pwa/icon-maskable-192.png"),
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "maskable",
        },
        {
            "src": static("core/pwa/icon-maskable-512.png"),
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ]




def _can_access_business(user, business):
    if user.is_superuser:
        return True
    return UserBusiness.objects.filter(
        user=user,
        business=business,
        active=True,
    ).exists()

def _manifest_response(payload, *, tenant=False):
    response = JsonResponse(payload)
    response["Content-Type"] = "application/manifest+json; charset=utf-8"
    # A short private cache prevents browsers from re-querying tenant metadata
    # during ordinary navigation while still allowing branding changes to settle
    # quickly. The public INPROFIC manifest is stable for longer.
    response["Cache-Control"] = "private, max-age=300" if tenant else "public, max-age=3600"
    if tenant:
        patch_vary_headers(response, ("Cookie",))
    return response


@require_GET
def manifest(request):
    return _manifest_response(
        {
            "id": "/pwa/inprofic",
            "name": "INPROFIC",
            "short_name": "INPROFIC",
            "description": "Inventory, operations, finance and commerce in one connected workspace.",
            "start_url": reverse("marketing_home"),
            "scope": "/",
            "display": "standalone",
            "background_color": "#FFF1E8",
            "theme_color": "#050733",
            "categories": ["business", "productivity", "finance"],
            "icons": _icons(),
            "prefer_related_applications": False,
        }
    )


@login_required
@require_GET
def tenant_manifest(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    if not _can_access_business(request.user, business):
        return HttpResponse("Business access unavailable.", status=403)
    description = business.tagline.strip() or "Your INPROFIC business workspace."
    return _manifest_response(
        {
            "id": f"/pwa/tenant/{business.slug}",
            "name": business.name,
            "short_name": business.name[:30],
            "description": description,
            "start_url": reverse("pwa_launch", kwargs={"business_slug": business.slug}),
            "scope": "/",
            "display": "standalone",
            # Keep the operating-system launch canvas aligned with the light
            # orange icon tile; tenant colours still brand the browser chrome.
            "background_color": "#FFF1E8",
            "theme_color": business.background_color,
            "categories": ["business", "productivity", "finance"],
            # Installed app artwork remains INPROFIC by design. The optional
            # tenant logo is storefront-only and is never used as a PWA icon.
            "icons": _icons(),
            "prefer_related_applications": False,
        },
        tenant=True,
    )


@require_GET
def service_worker(request):
    static_assets = [
        static("core/pwa/icon-180.png"),
        static("core/pwa/icon-192.png"),
        static("core/pwa/icon-512.png"),
        static("core/pwa/icon-maskable-192.png"),
        static("core/pwa/icon-maskable-512.png"),
    ]
    offline_url = reverse("pwa_offline")
    build_version = _pwa_build_version()
    cache_name = f"inprofic-static-{build_version}"
    js = f"""
const APP_VERSION = {json.dumps(build_version)};
const CACHE_NAME = {json.dumps(cache_name)};
const OFFLINE_URL = {json.dumps(offline_url)};
const PRECACHE_URLS = {json.dumps([offline_url, *static_assets])};

self.addEventListener('install', (event) => {{
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)));
}});

self.addEventListener('message', (event) => {{
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
  if (event.data && event.data.type === 'GET_VERSION' && event.source) {{
    event.source.postMessage({{ type: 'INPROFIC_SW_VERSION', version: APP_VERSION }});
  }}
}});

self.addEventListener('activate', (event) => {{
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key.startsWith('inprofic-static-') && key !== CACHE_NAME)
          .map((key) => caches.delete(key))
    )).then(() => self.clients.claim())
  );
}});

self.addEventListener('push', (event) => {{
  let data = {{}};
  try {{ data = event.data ? event.data.json() : {{}}; }} catch (_) {{ data = {{}}; }}
  if (data.type !== 'commerce.notification') return;
  event.waitUntil((async () => {{
    const windows = await self.clients.matchAll({{ type: 'window', includeUncontrolled: true }});
    // A visible INPROFIC page already receives the same durable notice over
    // the Commerce WebSocket, so don't create a duplicate operating-system alert.
    if (windows.some((client) => client.visibilityState === 'visible')) return;
    const target = new URL(data.url || '/commerce/', self.location.origin).href;
    await self.registration.showNotification(data.title || 'INPROFIC', {{
      body: data.body || '',
      icon: data.icon || '/static/core/pwa/icon-192.png',
      badge: data.badge || '/static/core/pwa/icon-192.png',
      tag: data.id ? `${{data.channel || 'commerce'}}-${{data.id}}` : `${{data.channel || 'commerce'}}-notification`,
      renotify: true,
      data: {{ url: target, notificationId: data.id || '' }},
    }});
  }})());
}});

self.addEventListener('notificationclick', (event) => {{
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/commerce/', self.location.origin).href;
  event.waitUntil((async () => {{
    const windows = await self.clients.matchAll({{ type: 'window', includeUncontrolled: true }});
    for (const client of windows) {{
      if ('focus' in client) {{
        try {{ if ('navigate' in client) await client.navigate(target); }} catch (_) {{}}
        return client.focus();
      }}
    }}
    return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
  }})());
}});

self.addEventListener('fetch', (event) => {{
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Dynamic application pages always go to the network. No authenticated
  // HTML, APIs, sessions, media, inventory or financial data are cached.
  if (request.mode === 'navigate') {{
    event.respondWith(
      fetch(request, {{ cache: 'no-store' }}).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }}

  // Static assets are network-first while online so a deployment never serves
  // stale CSS/JS from the service worker. Cached copies are only a fallback.
  if (url.pathname.startsWith('/static/')) {{
    event.respondWith((async () => {{
      try {{
        const response = await fetch(request);
        if (response && response.ok) {{
          const cache = await caches.open(CACHE_NAME);
          await cache.put(request, response.clone());
        }}
        return response;
      }} catch (_) {{
        const cached = await caches.match(request);
        return cached || Response.error();
      }}
    }})());
  }}
}});
""".strip()
    response = HttpResponse(js, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
def offline(request):
    # Render without the request context so the offline fallback never triggers
    # tenant/session/permission database work.
    html = render_to_string("pwa/offline.html", request=None)
    response = HttpResponse(html)
    response["Cache-Control"] = "public, max-age=3600"
    return response


@login_required
@require_GET
def launch(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    if not _can_access_business(request.user, business):
        return HttpResponse("Business access unavailable.", status=403)
    request.session["active_business_id"] = business.pk
    return redirect("dashboard")
