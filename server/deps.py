"""FastAPI ``Depends`` dependencies shared by the ``routes_*.py`` modules.

A dependency runs before FastAPI validates the request's body, query and form
fields, so a check that raises (401/503 for the account, say) would then come
before a 422 instead of after it. Only a route with nothing to validate beyond
string path parameters can therefore take one without changing which status an
invalid, unauthenticated request gets. The other handlers keep their
preambles in the body, where the order of checks is part of the contract
(quota after admission, token checks before the upload is read, and so on).

Like the route modules, these factories receive the ``main`` module and reach
``main``'s names at call time, so a test that patches one is seen here.
"""

from collections.abc import Awaitable, Callable

from fastapi import Request


def account_required(m) -> Callable[[Request], Awaitable[object]]:
    """Dependency that resolves the signed-in account or raises 401/503.

    Same errors and headers as calling ``m._require_account(request)`` first
    thing in the handler; the result is the ``AccountPrincipal``.
    """

    async def require_account(request: Request):
        return await m._require_account(request)

    return require_account
