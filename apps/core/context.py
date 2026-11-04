"""Holds the current request's resolved Business in a contextvar, set by
BusinessMiddleware after tenant resolution — never guess it elsewhere.
Not populated outside a request (management commands, shell): code running
there should query Business explicitly and use raw_objects."""
from contextvars import ContextVar

_current_business_id = ContextVar("current_business_id", default=None)
_request_cache = ContextVar("request_cache", default=None)
_ledger_scope = ContextVar("ledger_scope", default=None)
_ledger_cutover_date = ContextVar("ledger_cutover_date", default=None)
_ledger_status = ContextVar("ledger_status", default=None)
_ledger_bypass = ContextVar("ledger_bypass", default=False)
_raw_stock_reads = ContextVar("ledger_raw_stock_reads", default=False)


def set_current_business(business):
    _current_business_id.set(business.pk if business else None)


def get_current_business_id():
    return _current_business_id.get()


def set_ledger_context(scope=None, cutover_date=None, status=None):
    _ledger_scope.set(scope)
    _ledger_cutover_date.set(cutover_date)
    _ledger_status.set(status)


def get_ledger_scope():
    return _ledger_scope.get()


def get_ledger_cutover_date():
    return _ledger_cutover_date.get()


def get_ledger_status():
    return _ledger_status.get()


def set_ledger_bypass(value):
    return _ledger_bypass.set(bool(value))


def ledger_bypass_active():
    return _ledger_bypass.get()


def set_raw_stock_reads(value):
    """While True, ``stock`` attributes return stored values (used during save())."""
    return _raw_stock_reads.set(bool(value))


def raw_stock_reads_active():
    return _raw_stock_reads.get()


def begin_request_cache():
    """Create isolated, request-lifetime storage for repeated service lookups."""
    return _request_cache.set({})


def end_request_cache(token):
    _request_cache.reset(token)


def get_request_cache():
    return _request_cache.get()
