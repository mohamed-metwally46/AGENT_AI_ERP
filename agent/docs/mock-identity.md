# The mock identity system

Development-only multi-user identity and permissions, and how the real ERP
adapter replaces it.

**None of this may authorise a real deployment.** The roster is committed to
this repository, its credentials are static and non-expiring, and one of them
grants organisation scope. Two independent checks refuse it in production; see
[Production refusal](#production-refusal).

---

## The chain

```
credential            access_tokens.dev.yaml     AccessTokenPort.identify()
    ↓
identity (user_id)    identity.dev.yaml: users   PrincipalResolverPort.resolve()
    ↓
roles                 identity.dev.yaml: roles
    ↓
permissions           RoleCatalogue.resolve()    ← the multi-role rule
    ↓
Principal             + org placement from the database
    ↓
SecurityContext       Principal.to_security_context()
    ↓
Agent ──┬── SQL tools     scope → predicate, permissions → column list
        └── RAG tools     scope → store-side metadata filter
```

Every step is a refusal point. No step has a fallback: an unknown credential, an
unknown identity, an unknown role and an unplaceable employee number each raise,
and none of them degrades to a narrower principal. An authentication failure that
returns a working principal is the worst outcome available, so it is the one
outcome nothing here can produce.

---

## The mock users

Seven, keyed on `user_id` — which is what `users.id` supplies after cutover, and
deliberately not an employee id.

| user | employee | roles | scope | contact PII | compensation |
|---|---|---|---|---|---|
| `1001` | EMP-0001 (CEO) | `admin` | organisation | ✅ | ✅ |
| `1002` | EMP-0002 (HR director) | `hr_manager` | branch | ✅ | — |
| `1048` | EMP-0048 (HR staff) | `hr_employee` | department | ✅ | — |
| `1008` | EMP-0008 (Sales manager) | `sales_manager` | team | — | — |
| `1016` | EMP-0016 (Sales IC) | `employee` | self | — | — |
| `1006` | EMP-0006 (HR manager) | `hr_employee` + `payroll_officer` | department | ✅ | ✅ |
| `1003` | EMP-0003 (Finance director) | `employee` + `payroll_officer` | department | — | ✅ |

The last two are the multi-role cases, and they demonstrate different halves of
the rule:

- **`1006`** holds two department-scoped roles. Neither grants both contact PII
  and compensation; held together they do. *Permissions are the union.*
- **`1003`** holds `employee` (self) and `payroll_officer` (department). *Scope
  is the maximum* — so adding a role can only ever widen access, never narrow
  it.

Contact PII and compensation are independent grants: `1002` reaches an entire
branch of rows and still may not see a salary in any of them, while `1003`
reaches one department and may. Scope decides *which rows*; permissions decide
*which columns*. Neither substitutes for the other.

### The roles

| role | scope | grants |
|---|---|---|
| `admin` | organisation | employee, contact_pii, compensation, leave, attendance, documents |
| `hr_manager` | branch | employee, contact_pii, leave, attendance, documents |
| `hr_employee` | department | employee, contact_pii, leave, attendance, documents |
| `sales_manager` | team | employee, leave, attendance, documents |
| `payroll_officer` | department | employee, compensation, documents |
| `employee` | self | employee, leave, attendance, documents |

All grants are at level `read`. This agent has no write code path at all
(Plan.md §8.2, barrier 3), so nothing ever asks for more.

### What no role unlocks

Identity documents, banking details, and health-or-belief data are in
`NEVER_RETURNED`. There is deliberately no role, no permission and no
relationship that reaches them — not `admin`, and not the subject reading their
own record. The moment such a role exists it becomes the role everybody has.

---

## Credentials

| token | user |
|---|---|
| `dev-token-admin-1001` | admin |
| `dev-token-hr-manager-1002` | HR manager |
| `dev-token-hr-employee-1048` | HR employee |
| `dev-token-sales-manager-1008` | sales manager |
| `dev-token-employee-1016` | employee |
| `dev-token-hr-payroll-1006` | HR + payroll (multi-role) |
| `dev-token-payroll-employee-1003` | employee + payroll (multi-role) |

```bash
curl -s localhost:8000/ask \
  -H "Authorization: Bearer dev-token-employee-1016" \
  -H "Content-Type: application/json" \
  -d '{"question": "How many people work in my department?"}'
```

Asking the same question with `dev-token-admin-1001` reaches the agent with a
different `SecurityContext` and returns a different answer. Nothing in the
request body changes that: `scope`, `role_names`, `permissions` and the rest are
rejected by the request schema outright, and there is no dependency through
which a route could obtain both a context and a body field that influences it.

---

## Why the roster names `EMP-0001` and not a uuid

`employees.employee_id` is `gen_random_uuid()`, so every `docker compose down -v`
regenerates it. The fixture this replaced held those uuids and carried a
regeneration ritual in its header; anyone who re-seeded got a 401 that looked
like a bad credential.

The roster now names `employee_number`, the stable business key, and the
employee id, department and branch are looked up once during `container.warm()`
through `OrgPlacementPort`. An employee number the database does not have is a
**start-up failure**, not a failed question.

That lookup is the one query in the package with no `SecurityContext`, because it
runs while one is being built. It is safe for reasons that are structural rather
than incidental: it selects four identifier columns and nothing a field policy
would restrict, and it is called once at start-up with a fixed set of numbers
from the fixture, never with anything a caller supplied.

---

## Production refusal

Two checks, deliberately independent.

1. **`Container.principal_resolver` / `Container.access_tokens`** refuse to build
   when `APP_ENV=production` and the corresponding path is unset.
2. **`bootstrap.run_startup_checks`** refuses the same thing again,
   unconditionally.

The second exists because the first is a `cached_property` — it only refuses when
something touches it, so a process that served a health check before its first
authenticated question would have started successfully with mock credentials
loaded. The boot check has no such hole.

```
ERP_PRINCIPAL_FIXTURE_PATH      # the roster
ERP_ACCESS_TOKEN_FIXTURE_PATH   # the credentials
```

Overriding one and not the other is not enough, and start-up says so by name.

---

## Replacing this with the real ERP adapter

The seam is `PrincipalResolverPort`. Everything above it — the agent, the tools,
the use case, the API — is typed against the port and cannot tell which
implementation it got. `tests/architecture/test_layering.py` enforces that.

### What the cutover is

```python
# core/container.py — the entire change
@cached_property
def principal_resolver(self) -> PrincipalResolverPort:
    return ErpIdentityResolver(self.data_source)
```

`ErpIdentityResolver.resolve()` returns a `Principal`, built from two queries:

```sql
-- placement, replacing OrgPlacementPort
SELECT u.id, u.employee_id, e.dept_id, e.branch_id
  FROM users u
  JOIN employees e ON e.employee_id = u.employee_id
 WHERE u.id = %(identity)s;

-- the grant, replacing the fixture's roles: and users: blocks
SELECT r.name, p.resource, p.action, rp.permission_level, ur.branch_id
  FROM user_roles ur
  JOIN roles r            ON r.id = ur.role_id
  JOIN role_permissions rp ON rp.role_id = r.id
  JOIN permissions p       ON p.id = rp.permission_id
 WHERE ur.user_id = %(identity)s;
```

### What does not change

The multi-role rule is **not** in the adapter. `RoleCatalogue.resolve()` lives in
the domain, so the ERP adapter builds `Role` objects from its rows and calls the
same function this one does. There is no second reading of "what does holding two
roles mean" to drift out of step, and `tests/unit/domain/test_role_catalogue.py`
covers both adapters at once.

Nothing in `application/`, `interfaces/`, or the tools changes. Neither does
`field_policy.py`: it asks for `hr.contact_pii:read`, and where that grant comes
from is not its concern.

### What has to be agreed first

Three things, and none of them is code:

- **Gap S2 — the `permissions.resource` vocabulary.** Plan.md records that it is
  not enumerated anywhere. `domain/models/authorization.Resource` enumerates
  *this agent's* side of it: the six things it is capable of reading. The
  cutover needs a mapping from the ERP's resource strings onto those six, agreed
  with the ERP owners. A resource that maps to nothing must fail loudly — which
  it will, because `Resource` is an enum and an unrecognised value does not load.
- **`permission_level` semantics.** This implementation treats `none` as the
  absence of a grant and merges by taking the highest level, so holding an extra
  role never removes access. If the ERP intends `none` as a deny that overrides
  other roles, `PermissionSet.merge` is the one function that changes.
- **Gap S1 — department scope.** `user_roles` scopes by branch only, so
  `DEPARTMENT` scope is derived from `employees.dept_id` rather than granted.
  That derivation is documented, not invented, and it stays true after cutover.

### The reporting tree

`manager_of` is still not wired, so `TEAM` scope narrows to the principal
themselves. That is under-permission — the safe direction — and closing it needs
a recursive query over `employees.manager_id`. It is orthogonal to this work and
unchanged by it.

---

## Related

- `docs/erp-cutover.md` — the data path: pointing the agent at a real ERP.
- Plan.md §8.1 (the authorization model), §8.3 (field restrictions),
  §8.5 (gaps S1, S2, S6).
