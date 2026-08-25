"""Port: where an employee sits in the organisation.

The one lookup that has to happen *before* a
:class:`~erp_agent.domain.models.security_context.SecurityContext` exists, and
therefore the one that cannot be filtered by it. That sounds alarming, so it is
worth being precise about why it is not:

*It returns no data about a person.* Four identifiers -- the employee number,
the employee id, the department and the branch. No name, no contact detail, no
compensation, nothing in
:data:`~erp_agent.domain.policies.field_policy.NEVER_RETURNED`. Nobody's privacy
turns on which department id a row carries.

*It is not reachable from a request.* It is called once, during start-up, with
the fixed set of employee numbers the identity store names. No caller-supplied
value reaches it, so there is nothing here to probe. The mock adapter enforces
that by looking every placement up in :meth:`prepare` and never again.

*It is the seam the ERP resolver needs anyway.* The real resolver joins
``users.employee_id -> employees`` for exactly these columns (Plan.md section
8.1). Making it a port now means the cutover replaces an implementation rather
than inventing a query.

``placements`` is asynchronous because the implementation is a database read.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["OrgPlacement", "OrgPlacementPort"]


@dataclass(frozen=True, slots=True)
class OrgPlacement:
    """Where one employee sits.

    Attributes:
        employee_number: The stable business key, e.g. ``EMP-0001``. Stable
            across re-seeds, which is why the identity store names this rather
            than the surrogate id.
        employee_id: The surrogate key the rest of the system uses. Regenerated
            by every re-seed, which is why nothing writes it down.
        dept_id: Their department, the source of ``DEPARTMENT`` scope. Derived
            from the employee record rather than granted by RBAC, because
            ``user_roles`` scopes by branch only (Plan.md section 8.5, gap S1).
        branch_id: Their branch.
    """

    employee_number: str
    employee_id: str
    dept_id: str | None = None
    branch_id: str | None = None

    def __post_init__(self) -> None:
        """Validate the placement identifies somebody.

        Raises:
            ValueError: If either identifier is blank. A placement with no
                employee id would produce a principal whose scope predicate
                matches nothing -- or, with a different predicate bug,
                everything.
        """
        if not self.employee_number.strip():
            raise ValueError("employee_number must be a non-empty string")
        if not self.employee_id.strip():
            raise ValueError("employee_id must be a non-empty string")


class OrgPlacementPort(ABC):
    """Looks up where employees sit, by their stable business key."""

    @abstractmethod
    async def placements(self, employee_numbers: frozenset[str]) -> Mapping[str, OrgPlacement]:
        """Resolve a set of employee numbers to their org placement.

        Args:
            employee_numbers: The numbers to look up. A fixed set, known at
                start-up; never anything a caller supplied.

        Returns:
            A mapping keyed by employee number. Numbers that match no employee
            are **absent** rather than present-and-empty, so a caller must
            decide what a missing one means instead of receiving a placement
            with nothing in it. The mock identity adapter treats it as a
            start-up failure.

        Raises:
            DataSourceUnavailableError: If the database is unreachable.
        """
