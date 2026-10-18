"""Per-request context (request id, client address) available anywhere,
e.g. to the audit log, without threading it through every function call."""
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)
user_agent_var: ContextVar[str | None] = ContextVar("user_agent", default=None)
# A dict shared by everything handling one request. Sync endpoints run in a
# worker thread with a *copy* of the context, so values they need to hand back
# (e.g. the authenticated user id for the access log) go into this dict.
request_state_var: ContextVar[dict | None] = ContextVar("request_state", default=None)


def request_id() -> str | None:
    return request_id_var.get()


def client_ip() -> str | None:
    return client_ip_var.get()


def user_agent() -> str | None:
    return user_agent_var.get()


def user_id() -> int | None:
    state = request_state_var.get()
    return state.get("user_id") if state else None


def set_user_id(value: int) -> None:
    state = request_state_var.get()
    if state is not None:
        state["user_id"] = value
