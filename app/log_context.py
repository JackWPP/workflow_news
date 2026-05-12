from __future__ import annotations

import contextvars

run_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("run_id", default=None)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
