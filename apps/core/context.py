"""Holds the current request's resolved Business in a contextvar, set by
BusinessMiddleware after tenant resolution — never guess it elsewhere.
Not populated outside a request (management commands, shell): code running
there should query Business explicitly and use raw_objects."""
from contextvars import ContextVar

_current_business_id = ContextVar("current_business_id", default=None)
_request_cache = ContextVar("request_cache", default=None)
_ledger_scope = ContextVar("ledger_scope", default=None)
_ledger_cutover_date = ContextVar("ledger_cutover_date", default=None)


def set_current_business(business):
    _current_business_id.set(business.pk if business else None)


def get_current_business_id():
    return _current_business_id.get()


def set_ledger_context(scope=None, cutover_date=None):
    _ledger_scope.set(scope)
    _ledger_cutover_date.set(cutover_date)


def get_ledger_scope():
    return _ledger_scope.get()


def get_ledger_cutover_date():
    return _ledger_cutover_date.get()


def begin_request_cache():
    """Create isolated, request-lifetime storage for repeated service lookups."""
    return _request_cache.set({})


def end_request_cache(token):
    _request_cache.reset(token)


def get_request_cache():
    return _request_cache.get()
