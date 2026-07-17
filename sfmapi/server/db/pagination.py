"""AIP-158 keyset-pagination helpers.

Every list surface in the API paginates the same way: fetch/slice
``page_size + 1`` items, return the first ``page_size``, and — iff the
extra item existed — emit a ``next_page_token`` derived from the last
*returned* item. These two helpers are the single home of that idiom:

- :func:`paginate_keyset` — SQL keyset pagination over an ORM ``select()``
  with a string primary-key cursor column.
- :func:`paginate_sequence` — the same page/token contract over an
  already-filtered, already-ordered in-memory sequence.

Do not reintroduce ``page_size + 1`` literals at call sites; a lean-audit
acceptance check greps for exactly that.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute


async def paginate_keyset[T](
    session: AsyncSession,
    stmt: Select[tuple[T]],
    *,
    pk: InstrumentedAttribute[str],
    page_size: int,
    page_token: str | None = None,
    descending: bool = False,
) -> tuple[list[T], str | None]:
    """Execute ``stmt`` with keyset pagination on ``pk``.

    The helper owns ordering and the cursor predicate so they cannot
    diverge: ascending order pairs with ``pk > page_token``, descending
    with ``pk < page_token``. ``stmt`` must therefore carry only its
    filters — no ``order_by`` / ``limit``. The returned token is the
    ``pk`` value of the last row on the page (AIP-158 exclusive cursor).
    """
    stmt = stmt.order_by(pk.desc() if descending else pk.asc())
    if page_token:
        stmt = stmt.where(pk < page_token if descending else pk > page_token)
    stmt = stmt.limit(page_size + 1)
    rows: list[T] = list((await session.execute(stmt)).scalars().all())
    next_page_token: str | None = None
    if len(rows) > page_size:
        next_page_token = getattr(rows[page_size - 1], pk.key)
        rows = rows[:page_size]
    return rows, next_page_token


def paginate_sequence[T](
    rows: Sequence[T],
    *,
    page_size: int,
    token_for: Callable[[T], str],
) -> tuple[list[T], str | None]:
    """Slice one page off an already-filtered, already-ordered sequence.

    Callers apply their own ``page_token`` predicate *before* calling
    (token encodings vary per surface); ``token_for`` turns the last
    returned item into the next cursor.
    """
    page = list(rows[: page_size + 1])
    next_page_token: str | None = None
    if len(page) > page_size:
        next_page_token = token_for(page[page_size - 1])
        page = page[:page_size]
    return page, next_page_token
