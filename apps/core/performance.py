"""Low-overhead request performance instrumentation for production diagnostics."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import logging
import time

from django.conf import settings
from django.db import connections
from django.middleware.gzip import GZipMiddleware


logger = logging.getLogger("inprofic.performance")


@dataclass
class DatabaseTiming:
    """Aggregate SQL execution timing without retaining SQL text or parameters."""

    query_count: int = 0
    duration_seconds: float = 0.0
    max_query_seconds: float = 0.0
    slow_query_count: int = 0


@dataclass
class SectionTiming:
    """Named request subsection timing used to isolate non-SQL work."""

    wall_seconds: float = 0.0
    sql_seconds: float = 0.0
    query_count: int = 0
    calls: int = 0


@contextmanager
def performance_section(request, name: str):
    """Measure one static, non-sensitive request section when diagnostics are on.

    The middleware owns the backing request attributes. Outside diagnostic mode
    this is effectively a no-op, so views can leave these spans in production
    without adding database work or retaining request/customer data.
    """

    sections = getattr(request, "_performance_sections", None)
    db_timing = getattr(request, "_performance_db_timing", None)
    if sections is None or db_timing is None:
        yield
        return

    safe_name = "".join(
        char for char in str(name)[:64]
        if char.isalnum() or char in {"_", "-", "."}
    ) or "section"
    wall_started = time.perf_counter()
    sql_started = db_timing.duration_seconds
    query_started = db_timing.query_count
    try:
        yield
    finally:
        wall_elapsed = max(0.0, time.perf_counter() - wall_started)
        sql_elapsed = max(0.0, db_timing.duration_seconds - sql_started)
        query_delta = max(0, db_timing.query_count - query_started)
        timing = sections.setdefault(safe_name, SectionTiming())
        timing.wall_seconds += wall_elapsed
        timing.sql_seconds += sql_elapsed
        timing.query_count += query_delta
        timing.calls += 1


class QueryTimingWrapper:
    """Django execute_wrapper callback used only while diagnostics are enabled."""

    def __init__(self, timing: DatabaseTiming, *, slow_query_seconds: float):
        self.timing = timing
        self.slow_query_seconds = slow_query_seconds

    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            duration = time.perf_counter() - started
            self.timing.query_count += 1
            self.timing.duration_seconds += duration
            self.timing.max_query_seconds = max(self.timing.max_query_seconds, duration)
            if duration >= self.slow_query_seconds:
                self.timing.slow_query_count += 1


class PerformanceDiagnosticMiddleware:
    """
    Measure dynamic request time and aggregate database execution time.

    The middleware is opt-in through PERF_DIAGNOSTICS. It never stores SQL,
    parameters, request bodies, cookies, user identifiers, or query strings.
    Only slow requests (or server errors) are emitted to the performance log.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = bool(getattr(settings, "PERF_DIAGNOSTICS", False))
        self.slow_request_ms = max(
            float(getattr(settings, "PERF_SLOW_REQUEST_MS", 500)),
            0.0,
        )
        self.slow_query_ms = max(
            float(getattr(settings, "PERF_SLOW_QUERY_MS", 100)),
            0.0,
        )
        self.server_timing = bool(getattr(settings, "PERF_SERVER_TIMING", True))
        self.excluded_prefixes = tuple(
            getattr(
                settings,
                "PERF_EXCLUDED_PREFIXES",
                ("/health/", "/static/", "/media/", "/ws/"),
            )
        )

    def __call__(self, request):
        if not self.enabled or self._excluded(request.path):
            return self.get_response(request)

        db_timing = DatabaseTiming()
        request._performance_db_timing = db_timing
        request._performance_sections = {}
        started = time.perf_counter()

        # Enter a wrapper for each configured database alias without opening a
        # database connection just for instrumentation. The wrapper is invoked
        # only if the request actually executes SQL on that connection.
        with ExitStack() as stack:
            for alias in connections:
                stack.enter_context(
                    connections[alias].execute_wrapper(
                        QueryTimingWrapper(
                            db_timing,
                            slow_query_seconds=self.slow_query_ms / 1000.0,
                        )
                    )
                )
            response = self.get_response(request)

        total_ms = (time.perf_counter() - started) * 1000.0
        sql_ms = db_timing.duration_seconds * 1000.0
        non_sql_ms = max(0.0, total_ms - sql_ms)
        max_sql_ms = db_timing.max_query_seconds * 1000.0
        status_code = getattr(response, "status_code", 500)

        if self.server_timing:
            diagnostic_value = (
                f'app;dur={total_ms:.1f}, '
                f'sql;dur={sql_ms:.1f};desc="{db_timing.query_count} queries", '
                f'sqlmax;dur={max_sql_ms:.1f}'
            )
            existing = response.headers.get("Server-Timing")
            response.headers["Server-Timing"] = (
                f"{existing}, {diagnostic_value}" if existing else diagnostic_value
            )

        if total_ms >= self.slow_request_ms or status_code >= 500:
            # Prefer the resolved route name instead of the concrete URL. This
            # keeps IDs/slugs and the query string out of production logs while
            # still identifying the slow application surface.
            logger.warning(
                "slow_request method=%s route=%s status=%s total_ms=%.1f sql_ms=%.1f non_sql_ms=%.1f queries=%s max_sql_ms=%.1f slow_sql_queries=%s sections=%s",
                request.method,
                self._route_label(request),
                status_code,
                total_ms,
                sql_ms,
                non_sql_ms,
                db_timing.query_count,
                max_sql_ms,
                db_timing.slow_query_count,
                self._section_summary(request),
            )

        return response

    def _excluded(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.excluded_prefixes)

    @staticmethod
    def _section_summary(request) -> str:
        """Return bounded static timing labels as wall/non-SQL/query-count."""

        sections = getattr(request, "_performance_sections", None) or {}
        if not sections:
            return "-"
        rows = sorted(
            sections.items(),
            key=lambda item: item[1].wall_seconds,
            reverse=True,
        )[:10]
        return ",".join(
            f"{name}:{timing.wall_seconds * 1000.0:.1f}/"
            f"{max(0.0, timing.wall_seconds - timing.sql_seconds) * 1000.0:.1f}/"
            f"{timing.query_count}"
            for name, timing in rows
        )

    @staticmethod
    def _route_label(request) -> str:
        resolver_match = getattr(request, "resolver_match", None)
        view_name = getattr(resolver_match, "view_name", None) if resolver_match else None
        if view_name:
            return str(view_name)[:200]
        # Unresolved paths are reduced to a generic label rather than logging
        # user-controlled path content.
        return "unresolved"


class TextOnlyGZipMiddleware(GZipMiddleware):
    """Compress text responses (HTML, JSON, CSS, JS, XML, SVG, CSV) only.

    Dynamic pages in this app are large (hundreds of KB of HTML), so compressing
    them is the single biggest transfer saving on slow mobile connections.
    Already-compressed bodies (PDF, XLSX, images, audio, zip) are left alone so
    no CPU is wasted and downloads are untouched. WhiteNoise serves static files
    before this middleware runs, and Django's GZipMiddleware itself skips
    responses that already carry a Content-Encoding.
    """

    COMPRESSIBLE = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml", "application/manifest+json")

    def process_response(self, request, response):
        content_type = (response.get("Content-Type") or "").lower()
        if not content_type.startswith(self.COMPRESSIBLE):
            return response
        return super().process_response(request, response)

