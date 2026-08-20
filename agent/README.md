# ERP AI Agent

A read-only question-answering agent over the ERP: structured HR data from
PostgreSQL, policy documents from the existing Document RAG, and hybrid questions
that need both.

`../Plan.md` is the source of truth for the architecture, the phases and the
acceptance criteria. This README covers only how to run what exists.

## The one property that matters

The agent cannot write. Not "does not", *cannot* — enforced at four independent
layers (Plan.md section 8.2):

| # | Layer | Control |
|---|---|---|
| 1 | Database | `mock_hr_readonly` holds `SELECT` and `USAGE`, nothing else, and logs in with `default_transaction_read_only = on` |
| 2 | Session | Every unit of work opens `BEGIN READ ONLY` with a statement timeout |
| 3 | Application | `SqlStatement` refuses to be constructed from anything but a single reading statement; the data-source port has no write method |
| 4 | Interface | No `execute_sql` tool exists. The registry is a static allowlist |

Layers 1 and 2 are independent for a specific reason:
`default_transaction_read_only` is a `USERSET` GUC, so a client can switch it
off. The privilege grants are what still refuse the write when it does.

Prompt text is **not** a barrier and is not counted as one.

## Setup

```bash
cd agent
uv venv --python 3.14
uv pip install -e ".[dev]"
cp .env.example .env
```

The database must be running and must have the read-only role:

```bash
docker compose up -d                     # from the repository root
docker exec mock-hr-postgres psql -U hr_user -d mock_hr \
    -f /database/schema/hr/10_readonly_role.sql
```

The role script is idempotent, and a fresh volume applies it automatically via
`database/init/00_bootstrap.sql`.

## Running

```bash
uv run python -m erp_agent                 # resolved configuration, no network
uv run python -m erp_agent --check         # + prove the write barrier
uv run python -m erp_agent --list-settings # every variable read
```

`--check` attempts a write of every shape and exits non-zero unless the server
refused all of them.

## Development

```bash
make check       # everything CI runs
make test-unit   # fast, hermetic tests only
make arch        # layering contracts
```

Integration tests need the database; they skip themselves when
`ERP_DATABASE_URL` is unset or unreachable.
