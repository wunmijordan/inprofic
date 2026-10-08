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


def _icons(theme="light", platform=""):
    """Manifest icons.

    The splash is a fixed brand-navy canvas, so every large "any" icon is the
    light (white + orange) N. Small desktop sizes, which Chrome uses for the
    title bar and taskbar, follow the user's app theme instead. Mobile
    launchers draw their own light backdrop, so they get an opaque tile with
    the coloured N (maskable), identical in every theme.
    """
    splash_192 = {"src": static("core/pwa/icon-mark-on-dark-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"}
    splash_512 = {"src": static("core/pwa/icon-mark-on-dark-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"}
    if platform in ("windows", "desktop"):
        # Desktop installs (Windows, macOS, Linux, ChromeOS): OS app icons sit on light
        # surfaces, so the large "any" icons are the coloured navy + orange N, not the
        # white splash N. Small sizes (title bar, taskbar) follow the app theme.
        # Mobile never reaches this branch; its list below is unchanged.
        stem = "icon-mark-windows-on-dark" if theme == "dark" else "icon-mark-windows"
        small = [
            {"src": static(f"core/pwa/{stem}-{n}.png"), "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any"}
            for n in (32, 48, 64)
        ]
        large = [
            {"src": static("core/pwa/icon-mark-windows-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("core/pwa/icon-mark-windows-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ]
        return [*small, *large]
    return [
        {"src": static("core/pwa/icon-launcher-maskable-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
        {"src": static("core/pwa/icon-launcher-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        splash_192,
        splash_512,
        {"src": static("core/pwa/icon-mark-monochrome-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "monochrome"},
        {"src": static("core/pwa/icon-mark-monochrome-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "monochrome"},
    ]


def _launch_theme(request):
    """Theme for launch surfaces: the page-reported cookie wins over the URL.

    Browsers re-use the manifest URL they stored at install time for update
    checks, so a ``?theme=`` baked into that URL goes stale. The cookie is
    refreshed on every page view with the user's resolved theme.
    """
    cookie = request.COOKIES.get("inprofic_theme")
    if cookie in ("dark", "light"):
        return cookie
    return "dark" if request.GET.get("theme") == "dark" else "light"


SPLASH_BACKGROUND = "#050733"  # fixed brand navy; only the in-page launch screen is theme-aware


def _launch_background(request):
    return SPLASH_BACKGROUND


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
    # Colours and icon variant depend on the viewer's theme cookie, so the
    # response must never be shared between users or themes.
    response["Cache-Control"] = "private, max-age=300"
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
            "background_color": _launch_background(request),
            "theme_color": _launch_background(request),
            "categories": ["business", "productivity", "finance"],
            "icons": _icons(_launch_theme(request), request.GET.get("platform", "")),
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
            # The OS splash/title bar stays a neutral, theme-matched canvas so
            # the two-colour transparent N mark is always drawn on the
            # background its icon variant was designed for. Tenant colours
            # brand the workspace itself, never the launch surface.
            "background_color": _launch_background(request),
            "theme_color": _launch_background(request),
            "categories": ["business", "productivity", "finance"],
            # Installed app artwork remains INPROFIC by design. The optional
            # tenant logo is storefront-only and is never used as a PWA icon.
            "icons": _icons(_launch_theme(request), request.GET.get("platform", "")),
            "prefer_related_applications": False,
        },
        tenant=True,
    )


@require_GET
def favicon(request):
    """Browsers request /favicon.ico regardless of <link rel=icon>. Without a route
    every hit rendered the full 404 page (~12KB); redirect to the brand favicon."""
    response = redirect(static("core/brand/inprofic-favicon.png"))
    response["Cache-Control"] = "public, max-age=86400"
    return response


@require_GET
def service_worker(request):
    static_assets = [
        static("core/pwa/icon-mark-180.png"),
        static("core/pwa/icon-mark-on-dark-180.png"),
        static("core/pwa/icon-mark-192.png"),
        static("core/pwa/icon-launcher-maskable-192.png"),
        static("core/pwa/icon-launcher-maskable-512.png"),
        static("core/pwa/icon-launcher-apple-180.png"),
        static("core/pwa/icon-mark-512.png"),
        static("core/pwa/icon-mark-on-dark-192.png"),
        static("core/pwa/icon-mark-on-dark-512.png"),
        static("core/pwa/icon-mark-windows-192.png"),
        static("core/pwa/icon-mark-windows-32.png"),
        static("core/pwa/icon-mark-windows-48.png"),
        static("core/pwa/icon-mark-windows-64.png"),
        static("core/pwa/icon-mark-windows-on-dark-32.png"),
        static("core/pwa/icon-mark-windows-on-dark-48.png"),
        static("core/pwa/icon-mark-windows-on-dark-64.png"),
        static("core/pwa/icon-mark-windows-512.png"),
        static("core/pwa/icon-mark-monochrome-192.png"),
        static("core/pwa/icon-mark-monochrome-512.png"),
        static("core/brand/inprofic-wordmark-on-light.png"),
        static("core/brand/inprofic-wordmark-on-dark.png"),
    ]
    offline_url = reverse("pwa_offline")
    build_version = _pwa_build_version()
    cache_name = f"inprofic-static-{build_version}"
    js = f"""
const APP_VERSION = {json.dumps(build_version)};
const CACHE_NAME = {json.dumps(cache_name)};
const OFFLINE_URL = {json.dumps(offline_url)};
const PRECACHE_URLS = {json.dumps([offline_url, *static_assets])};
const PREFERENCE_CACHE = 'inprofic-pwa-preferences-v1';
const THEME_KEY = new URL('/__inprofic-pwa-theme__', self.location.origin).href;

async function savePreferredTheme(theme) {{
  if (theme !== 'dark' && theme !== 'light') return;
  const cache = await caches.open(PREFERENCE_CACHE);
  await cache.put(THEME_KEY, new Response(theme, {{ headers: {{ 'Content-Type': 'text/plain' }} }}));
}}

async function preferredTheme() {{
  try {{
    const cache = await caches.open(PREFERENCE_CACHE);
    const response = await cache.match(THEME_KEY);
    const theme = response ? await response.text() : '';
    return theme === 'dark' ? 'dark' : 'light';
  }} catch (_) {{
    return 'light';
  }}
}}

self.addEventListener('install', (event) => {{
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)));
}});

self.addEventListener('message', (event) => {{
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
  if (event.data && event.data.type === 'INPROFIC_THEME') {{
    event.waitUntil(savePreferredTheme(event.data.theme));
  }}
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
    const target = new URL(data.url || '/commerce/', self.location.origin).href;
    const theme = await preferredTheme();
    const themedIcon = theme === 'dark'
      ? (data.icon_dark || '/static/core/pwa/icon-mark-on-dark-192.png')
      : (data.icon_light || data.icon || '/static/core/pwa/icon-mark-192.png');
    const title = data.title || 'INPROFIC';
    const notificationData = {{ url: target, notificationId: data.id || '' }};
    const options = {{
      body: data.body || '',
      icon: themedIcon,
      badge: data.badge || '/static/core/pwa/icon-mark-monochrome-192.png',
      tag: data.id ? `${{data.channel || 'commerce'}}-${{data.id}}` : `${{data.channel || 'commerce'}}-notification`,
      data: notificationData,
    }};
    try {{
      await self.registration.showNotification(title, options);
    }} catch (_) {{
      // Keep a notification visible if a platform rejects optional icon or
      // metadata fields (some mobile implementations are stricter than desktop).
      await self.registration.showNotification(title, {{
        body: data.body || '',
        data: notificationData,
      }});
    }}
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
