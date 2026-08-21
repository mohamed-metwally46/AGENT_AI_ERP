# Pointing the agent at a real ERP

The migration `mock_hr` → ERP read replica, and the way back.

Plan.md section 13 recommends a **read replica with a read-only role**, for one
reason worth restating before any of the steps below: it makes the write
prohibition a physical property of the connection rather than a property of our
code. Every other barrier in this system is something we wrote and could get
wrong. That one is not.

---

## What actually changes

One environment variable.

```
ERP_DATABASE_URL=postgresql://erp_readonly:...@replica.internal:5432/erp
```

Nothing above `infrastructure/database` knows a table name, a connection string
or a dialect, and `tests/architecture/test_layering.py` asserts it. If a cutover
ever needs a second edit, that test failed first and this document is wrong.

Two things do **not** change and must be checked rather than assumed:

- the **grants** on the target, which are a property of the replica's role and
  not of this repository;
- the **schema**, which is what `--drift` is for.

---

## Before the cutover

### 1. Create the role on the replica

Mirror `database/schema/hr/10_readonly_role.sql`. The role must be `LOGIN
NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, hold
`SELECT` and nothing else, and carry:

```sql
ALTER ROLE erp_readonly SET default_transaction_read_only = on;
ALTER ROLE erp_readonly SET statement_timeout = '15s';
ALTER ROLE erp_readonly SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE erp_readonly SET lock_timeout = '5s';
```

`default_transaction_read_only` is a **USERSET** GUC: a client can turn it off.
It is barrier two of four, not the barrier. The grants are what a client cannot
argue with.

### 2. Prove the connection cannot write

```
ERP_DATABASE_URL=... uv run python -m erp_agent --check
```

This attempts an `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE`, `ALTER TABLE` and
`TRUNCATE` and requires every one to be refused. **Exit code 1 stops the
cutover.** It is not advisory: a process that cannot demonstrate the barrier
does not get to answer questions carefully, it stops.

### 3. Check for schema drift

```
ERP_DATABASE_URL=... uv run python -m erp_agent --drift
```

Reads only `information_schema` and `pg_catalog`, so it is exactly as safe to
run against production as the agent itself. It reports:

| Finding | Meaning | Blocks cutover |
|---|---|---|
| **Missing table** | The agent reads it and the target does not have it | Yes |
| **Present but not readable** | The table is there and the `GRANT SELECT` is not | Yes — and the fix is a grant, not a schema change |
| **Missing column** | A tool selects it and it is gone | Yes |
| **Changed type** | A date became text, or a number became a string | Yes |
| **Extra columns** | The target has more than the agent reads | No — explicit column lists mean it cannot read one by accident |

The contract it checks against is
`src/erp_agent/infrastructure/database/schema_contract.py`: ten tables, and only
the columns the query layer touches. The documented ERP has 85 tables and a real
one will drift in most of them; drift that matters is drift in what the agent
reads.

### 4. Run the evaluation against the target

```
ERP_DATABASE_URL=... uv run make eval
```

The golden set's control queries are written against the mock data, so the
**structured** figures will not match a real ERP and those rows are expected to
fail. What must still be clean are the security gates: cross-scope reads return
nothing, refusals hold, no restricted field appears. Those assert on behaviour,
not on values, and a failure there is a cutover blocker.

> Before the first real cutover, the structured cases need control queries and
> expected rows for the target. That is a data task for whoever owns the ERP,
> not a code change.

---

## The cutover

1. Deploy with the new `ERP_DATABASE_URL`, `ERP_KNOWLEDGE_ENABLED` unchanged.
2. Start-up runs the barrier proof itself — the process will not bind a port if
   the connection is not provably read-only.
3. Watch `GET /health`: `write_barrier: intact` is the property that was checked
   before anything was served.

---

## Rolling back

**The rollback is the same variable, changed back.** There is no migration to
undo, no state to reconcile and no data to restore, because the agent writes
nothing anywhere. That is the whole reason a read-only design was chosen, and it
is worth being explicit that "rollback" here is a deployment operation and not a
recovery one.

```
ERP_DATABASE_URL=postgresql://mock_hr_readonly:...@localhost:5433/mock_hr
```

Restart. Nothing else is required.

Two caveats that are real:

- **Conversation history is in-process.** A restart drops it. Follow-up
  questions ("and for Sales?") lose their antecedent; nothing else is affected,
  and no restricted payload was ever stored in it.
- **Principals and credentials are fixtures keyed to the mock data.** The dev
  `principals.dev.yaml` and `access_tokens.dev.yaml` carry `employee_id` values
  from one seeding of `mock_hr`. Pointing at a real ERP means those resolve to
  nobody, and every request gets a 401. See the blocker below.

---

## The part that is not ready, and why

**Principal resolution is fixture-backed** (Plan.md gap S6). `users` and
`user_roles` are Platform tables that `mock_hr` does not have, and this plan
does not create them. Until they exist on the target:

- `FixturePrincipalResolver` must be pointed at a fixture whose `employee_id`
  values are real on the target (`ERP_PRINCIPAL_FIXTURE_PATH`);
- `FixtureAccessTokens` must likewise, and the composition root **refuses the
  packaged dev credentials when `APP_ENV=production`** — deliberately, because
  they are static, non-expiring and committed to this repository.

The real implementation is a join `users.id → users.employee_id → employees` for
placement, and `user_roles → role_permissions → permissions` for the grant.
Neither can be written against a schema nobody has agreed, which is gap **S2**:
the `permissions.resource` vocabulary is not enumerated anywhere. Section 8.5 is
explicit that these are resolved with the ERP owners and that the plan does not
invent answers.

So the honest statement of readiness is: **the data path is ready to cut over
and the identity path is not.** A cutover today would read the right rows for a
principal the system cannot yet resolve from the target's own tables.
