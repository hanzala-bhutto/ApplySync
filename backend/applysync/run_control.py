from __future__ import annotations

import threading

# Cooperative cancellation for a running sync/full-audit, checked between
# emails in process_emails/process_full_audit's own loops. Python threads
# can't be force-killed, so "stop" can only ever mean "finish the email
# currently in flight, then stop before starting the next one" - not an
# instant abort. A single global flag is enough since web/sync.py's own lock
# already guarantees at most one sync/full-audit runs at a time (see
# _lock/_state there).
#
# Deliberately its own module, not observability.py: this is real control
# flow the pipeline's correctness depends on (a requested stop must actually
# stop the run), unlike observability.py's contents, which are explicitly
# documented as diagnostic-only and safe to no-op.
_cancel_requested = threading.Event()


def request_cancel() -> None:
    _cancel_requested.set()


def clear_cancel() -> None:
    _cancel_requested.clear()


def is_cancel_requested() -> bool:
    return _cancel_requested.is_set()
