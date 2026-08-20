-- =============================================================================
-- HR :: Leave (types, policies, balances, requests, accruals, calendar)
-- Derived from schema_split/HR/Leave.md
-- =============================================================================

-- --- leave_types -------------------------------------------------------------
-- NOTE: color_hex documented as bare `char` with default '#6366F1' and a
-- '^#[0-9A-Fa-f]{6}$' check; length inferred as 7.
CREATE TABLE leave_types (
    leave_type_id       uuid         NOT NULL DEFAULT gen_random_uuid(),
    leave_code          varchar(20)  NOT NULL,
    leave_name          varchar(100) NOT NULL,
    leave_name_local    varchar(100) NOT NULL,
    category            varchar(30)  NOT NULL,
    is_paid             boolean      NOT NULL DEFAULT true,
    requires_document   boolean      NOT NULL DEFAULT false,
    affects_attendance  boolean      NOT NULL DEFAULT true,
    color_hex           char(7)      NOT NULL DEFAULT '#6366F1',
    is_active           boolean      NOT NULL DEFAULT true,
    created_by          bigint       NOT NULL,
    created_at          timestamptz,
    updated_at          timestamptz,
    CONSTRAINT leave_types_pkey PRIMARY KEY (leave_type_id),
    CONSTRAINT leave_types_leave_code_key UNIQUE (leave_code),
    CONSTRAINT leave_types_color_hex_check CHECK (color_hex ~ '^#[0-9A-Fa-f]{6}$'),
    CONSTRAINT leave_types_category_check
        CHECK (category IN ('annual','sick','maternity','paternity','unpaid','emergency',
                            'bereavement','compassionate','hajj','study','other'))
);

CREATE INDEX idx_leave_types_category  ON leave_types (category);
CREATE INDEX idx_leave_types_is_active ON leave_types (is_active);

-- --- leave_policies ----------------------------------------------------------
CREATE TABLE leave_policies (
    policy_id                uuid         NOT NULL DEFAULT gen_random_uuid(),
    leave_type_id            uuid         NOT NULL,
    scope                    varchar(10)  NOT NULL DEFAULT 'all',
    scope_id                 uuid,
    annual_entitlement_days  numeric(5,1) NOT NULL DEFAULT 0,
    accrual_method           varchar(20)  NOT NULL DEFAULT 'annual_lump',
    accrual_rate             numeric(5,2),
    max_carry_forward_days   numeric(5,1),
    min_service_months       smallint     NOT NULL DEFAULT 0,
    max_consecutive_days     smallint,
    notice_days              smallint     NOT NULL DEFAULT 0,
    allow_negative_balance   boolean      NOT NULL DEFAULT false,
    gender_eligibility       varchar(10)  NOT NULL DEFAULT 'all',
    is_active                boolean      NOT NULL DEFAULT true,
    created_by               bigint       NOT NULL,
    created_at               timestamptz,
    updated_at               timestamptz,
    CONSTRAINT leave_policies_pkey PRIMARY KEY (policy_id),
    CONSTRAINT leave_policies_leave_type_id_fkey
        FOREIGN KEY (leave_type_id) REFERENCES leave_types (leave_type_id) ON DELETE RESTRICT,
    CONSTRAINT leave_policies_accrual_method_check
        CHECK (accrual_method IN ('annual_lump','monthly','anniversary','none')),
    CONSTRAINT leave_policies_entitlement_check CHECK (annual_entitlement_days >= 0),
    CONSTRAINT leave_policies_gender_check CHECK (gender_eligibility IN ('all','male','female')),
    CONSTRAINT leave_policies_scope_check CHECK (scope IN ('all','grade','dept')),
    CONSTRAINT leave_policies_scope_id_check CHECK (scope = 'all' OR scope_id IS NOT NULL)
);

CREATE INDEX idx_leave_policies_leave_type ON leave_policies (leave_type_id);
CREATE INDEX idx_leave_policies_scope      ON leave_policies (scope, scope_id);
-- The schema doc truncates this index definition mid-expression
-- ("leave_type_id, scope, COALESCE((scope_id"); reconstructed as the obvious
-- COALESCE-on-scope_id form, kept non-unique since the doc lists it under Indexes.
CREATE INDEX idx_leave_policies_type_scope_coalesce
    ON leave_policies (leave_type_id, scope, COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- --- leave_balances ----------------------------------------------------------
CREATE TABLE leave_balances (
    balance_id       uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id      uuid         NOT NULL,
    leave_type_id    uuid         NOT NULL,
    fiscal_year      smallint     NOT NULL,
    opening_balance  numeric(7,2) NOT NULL DEFAULT 0,
    accrued_ytd      numeric(7,2) NOT NULL DEFAULT 0,
    used_ytd         numeric(7,2) NOT NULL DEFAULT 0,
    pending_ytd      numeric(7,2) NOT NULL DEFAULT 0,
    created_at       timestamptz,
    updated_at       timestamptz,
    is_active        boolean      NOT NULL DEFAULT true,
    created_by       bigint,
    CONSTRAINT leave_balances_pkey PRIMARY KEY (balance_id),
    CONSTRAINT leave_balances_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE RESTRICT,
    CONSTRAINT leave_balances_leave_type_id_fkey
        FOREIGN KEY (leave_type_id) REFERENCES leave_types (leave_type_id) ON DELETE RESTRICT,
    CONSTRAINT leave_balances_non_negative_check
        CHECK (opening_balance >= 0 AND accrued_ytd >= 0 AND used_ytd >= 0 AND pending_ytd >= 0)
);

CREATE INDEX idx_leave_balances_emp_year        ON leave_balances (employee_id, fiscal_year);
CREATE INDEX idx_leave_balances_type_year       ON leave_balances (leave_type_id, fiscal_year);
CREATE INDEX idx_leave_balances_emp_type_year   ON leave_balances (employee_id, leave_type_id, fiscal_year);

-- --- leave_requests ----------------------------------------------------------
-- NOTE: the schema documents `approval_request_id` -> approval_requests.request_id,
-- which lives in the Platform domain and is out of scope for this HR-only mock
-- database. The column is replicated; the cross-domain FK constraint is not
-- created because the referenced table does not exist here.
CREATE TABLE leave_requests (
    request_id                uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id               uuid         NOT NULL,
    leave_type_id             uuid         NOT NULL,
    start_date                date         NOT NULL,
    end_date                  date         NOT NULL,
    working_days              numeric(5,2) NOT NULL,
    is_half_day               boolean      NOT NULL DEFAULT false,
    half_day_period           varchar(15),
    reason                    text,
    medical_certificate_path  text,
    medical_certificate_mime  varchar(100),
    status                    varchar(20)  NOT NULL DEFAULT 'pending',
    approval_request_id       uuid,
    reviewed_by_emp_id        uuid,
    reviewed_at               timestamptz,
    review_notes              text,
    is_active                 boolean      NOT NULL DEFAULT true,
    created_by                bigint       NOT NULL,
    created_at                timestamptz,
    updated_at                timestamptz,
    CONSTRAINT leave_requests_pkey PRIMARY KEY (request_id),
    CONSTRAINT leave_requests_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE RESTRICT,
    CONSTRAINT leave_requests_leave_type_id_fkey
        FOREIGN KEY (leave_type_id) REFERENCES leave_types (leave_type_id) ON DELETE RESTRICT,
    CONSTRAINT leave_requests_reviewed_by_emp_id_fkey
        FOREIGN KEY (reviewed_by_emp_id) REFERENCES employees (employee_id) ON DELETE SET NULL,
    CONSTRAINT leave_requests_dates_check CHECK (end_date >= start_date),
    CONSTRAINT leave_requests_half_day_check
        CHECK (half_day_period IS NULL OR half_day_period IN ('first_half','second_half')),
    CONSTRAINT leave_requests_status_check
        CHECK (status IN ('draft','pending','approved','rejected','returned','cancelled')),
    CONSTRAINT leave_requests_working_days_check CHECK (working_days > 0)
);

CREATE INDEX idx_leave_requests_dates       ON leave_requests (start_date, end_date);
CREATE INDEX idx_leave_requests_emp_status  ON leave_requests (employee_id, status);
CREATE INDEX idx_leave_requests_status      ON leave_requests (status);
CREATE INDEX idx_leave_requests_type        ON leave_requests (leave_type_id);
CREATE INDEX idx_leave_requests_emp_range   ON leave_requests (employee_id, start_date, end_date);

-- --- leave_accrual_logs ------------------------------------------------------
CREATE TABLE leave_accrual_logs (
    log_id           uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id      uuid         NOT NULL,
    leave_type_id    uuid         NOT NULL,
    balance_id       uuid         NOT NULL,
    accrual_date     date         NOT NULL,
    days_accrued     numeric(5,2) NOT NULL,
    balance_before   numeric(7,2) NOT NULL,
    balance_after    numeric(7,2) NOT NULL,
    accrual_formula  varchar(20)  NOT NULL,
    notes            text,
    run_id           uuid,
    created_at       timestamptz  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT leave_accrual_logs_pkey PRIMARY KEY (log_id),
    CONSTRAINT leave_accrual_logs_balance_id_fkey
        FOREIGN KEY (balance_id) REFERENCES leave_balances (balance_id) ON DELETE RESTRICT,
    CONSTRAINT leave_accrual_logs_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE RESTRICT,
    CONSTRAINT leave_accrual_logs_leave_type_id_fkey
        FOREIGN KEY (leave_type_id) REFERENCES leave_types (leave_type_id) ON DELETE RESTRICT,
    CONSTRAINT leave_accrual_logs_formula_check
        CHECK (accrual_formula IN ('annual_lump','monthly','anniversary','none'))
);

CREATE INDEX idx_leave_accrual_logs_emp_date ON leave_accrual_logs (employee_id, accrual_date);
CREATE INDEX idx_leave_accrual_logs_run_id   ON leave_accrual_logs (run_id);

-- --- leave_adjustments -------------------------------------------------------
CREATE TABLE leave_adjustments (
    adjustment_id    uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id      uuid         NOT NULL,
    leave_type_id    uuid         NOT NULL,
    fiscal_year      smallint     NOT NULL,
    adjustment_days  numeric(5,2) NOT NULL,
    reason           text         NOT NULL,
    adjusted_by      bigint       NOT NULL,
    created_at       timestamptz  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active        boolean      NOT NULL DEFAULT true,
    CONSTRAINT leave_adjustments_pkey PRIMARY KEY (adjustment_id),
    CONSTRAINT leave_adjustments_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE RESTRICT,
    CONSTRAINT leave_adjustments_leave_type_id_fkey
        FOREIGN KEY (leave_type_id) REFERENCES leave_types (leave_type_id) ON DELETE RESTRICT
);

CREATE INDEX idx_leave_adjustments_emp_year ON leave_adjustments (employee_id, fiscal_year);

-- --- working_day_calendars ---------------------------------------------------
-- NOTE: country_code documented as bare `char` with default 'EGY'; inferred char(3).
CREATE TABLE working_day_calendars (
    calendar_id      uuid        NOT NULL DEFAULT gen_random_uuid(),
    calendar_date    date        NOT NULL,
    is_working_day   boolean     NOT NULL,
    day_type         varchar(20) NOT NULL,
    description      text,
    country_code     char(3)     NOT NULL DEFAULT 'EGY',
    created_at       timestamptz,
    updated_at       timestamptz,
    is_active        boolean     NOT NULL DEFAULT true,
    created_by       bigint,
    CONSTRAINT working_day_calendars_pkey PRIMARY KEY (calendar_id),
    CONSTRAINT working_day_calendars_calendar_date_key UNIQUE (calendar_date),
    CONSTRAINT working_day_calendars_day_type_check
        CHECK (day_type IN ('weekday','weekend','public_holiday','company_holiday'))
);

CREATE INDEX idx_working_day_calendars_working ON working_day_calendars (is_working_day, calendar_date);
