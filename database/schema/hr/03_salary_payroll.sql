-- =============================================================================
-- HR :: Salary, accounting salary, payslips, payroll gaps (loans/bonuses/claims)
-- Derived from schema_split/HR/ Salary.md, AccountingSalary.md, Payslip.md,
--   PayrollGaps.md
-- =============================================================================

-- --- salary ------------------------------------------------------------------
CREATE TABLE salary (
    salary_id             uuid          NOT NULL DEFAULT gen_random_uuid(),
    employee_id           uuid          NOT NULL,
    job_grade_id          uuid,
    cost_centre_id        uuid,
    basic_salary          numeric(15,2) NOT NULL DEFAULT 0,
    housing_allowance     numeric(15,2) NOT NULL DEFAULT 0,
    transport_allowance   numeric(15,2) NOT NULL DEFAULT 0,
    food_allowance        numeric(15,2) NOT NULL DEFAULT 0,
    mobile_allowance      numeric(15,2) NOT NULL DEFAULT 0,
    other_allowance       numeric(15,2) NOT NULL DEFAULT 0,
    gross_salary          numeric(15,2) NOT NULL,
    income_tax_pct        numeric(5,2)  NOT NULL DEFAULT 0,
    social_insurance_pct  numeric(5,2)  NOT NULL DEFAULT 0,
    other_deduction       numeric(15,2) NOT NULL DEFAULT 0,
    net_salary            numeric(15,2) NOT NULL DEFAULT 0,
    currency_code         varchar(3)    NOT NULL DEFAULT 'EGP',
    pay_frequency         varchar(20)   NOT NULL DEFAULT 'monthly',
    pay_method            varchar(20)   NOT NULL DEFAULT 'bank_transfer',
    bank_name             varchar(100),
    bank_account          varchar(50),
    bank_iban             varchar(50),
    effective_date        date          NOT NULL,
    end_date              date,
    change_reason         varchar(30),
    change_type           varchar(20),
    approval_status       varchar(20)   NOT NULL DEFAULT 'pending_approval',
    approved_by           varchar(255),
    approved_date         date,
    is_current            boolean       NOT NULL DEFAULT false,
    created_by            bigint,
    created_at            timestamptz,
    updated_at            timestamptz,
    is_active             boolean       NOT NULL DEFAULT true,
    CONSTRAINT salary_pkey PRIMARY KEY (salary_id),
    CONSTRAINT salary_cost_centre_id_fkey
        FOREIGN KEY (cost_centre_id) REFERENCES cost_centers (cost_centre_id) ON DELETE SET NULL,
    CONSTRAINT salary_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE RESTRICT,
    CONSTRAINT salary_job_grade_id_fkey
        FOREIGN KEY (job_grade_id) REFERENCES job_grades (job_grade_id) ON DELETE SET NULL,
    CONSTRAINT salary_approval_status_check
        CHECK (approval_status IN ('pending_approval','approved','rejected')),
    CONSTRAINT salary_basic_salary_check CHECK (basic_salary >= 0),
    CONSTRAINT salary_change_reason_check
        CHECK (change_reason IS NULL OR change_reason IN ('hire','promotion','annual_review','correction','restructure','market_adjustment')),
    CONSTRAINT salary_change_type_check
        CHECK (change_type IS NULL OR change_type IN ('increase','decrease','correction')),
    CONSTRAINT salary_end_date_check CHECK (end_date IS NULL OR end_date >= effective_date),
    CONSTRAINT salary_pay_frequency_check
        CHECK (pay_frequency IN ('weekly','bi_weekly','semi_monthly','monthly')),
    CONSTRAINT salary_pay_method_check
        CHECK (pay_method IN ('bank_transfer','cash','cheque','wallet'))
);

CREATE INDEX idx_salary_cost_centre_id  ON salary (cost_centre_id);
CREATE INDEX idx_salary_effective_date  ON salary (effective_date);
CREATE INDEX idx_salary_employee_id     ON salary (employee_id);
CREATE INDEX idx_salary_emp_is_current  ON salary (employee_id, is_current);

-- --- accounting_salary -------------------------------------------------------
-- NOTE: pay_period and currency_code are documented as bare `char`. Lengths
-- inferred as 7 ('YYYY-MM', matching the varchar(7) period columns elsewhere)
-- and 3 (ISO-4217) respectively.
CREATE TABLE accounting_salary (
    acc_salary_id        uuid          NOT NULL DEFAULT gen_random_uuid(),
    cost_centre_id       uuid          NOT NULL,
    dept_id              uuid,
    branch_id            uuid,
    pay_period           char(7)       NOT NULL,
    fiscal_year          smallint      NOT NULL,
    fiscal_month         smallint      NOT NULL,
    currency_code        char(3)       NOT NULL,
    gross_salary_budget  numeric(15,2) NOT NULL DEFAULT 0,
    gross_salary_actual  numeric(15,2) NOT NULL DEFAULT 0,
    net_salary_actual    numeric(15,2) NOT NULL DEFAULT 0,
    allowances_total     numeric(15,2) NOT NULL DEFAULT 0,
    deductions_total     numeric(15,2) NOT NULL DEFAULT 0,
    employer_contrib     numeric(15,2) NOT NULL DEFAULT 0,
    posting_status       varchar(20)   NOT NULL DEFAULT 'draft',
    gl_account_code      varchar(50),
    journal_ref          varchar(50),
    posted_date          date,
    posted_by            varchar(200),
    created_by           bigint,
    created_at           timestamptz,
    updated_at           timestamptz,
    is_active            boolean       NOT NULL DEFAULT true,
    CONSTRAINT accounting_salary_pkey PRIMARY KEY (acc_salary_id),
    CONSTRAINT accounting_salary_cc_period_key UNIQUE (cost_centre_id, pay_period),
    CONSTRAINT accounting_salary_branch_id_fkey
        FOREIGN KEY (branch_id) REFERENCES organization_branches (branch_id) ON DELETE RESTRICT,
    CONSTRAINT accounting_salary_cost_centre_id_fkey
        FOREIGN KEY (cost_centre_id) REFERENCES cost_centers (cost_centre_id) ON DELETE RESTRICT,
    CONSTRAINT accounting_salary_dept_id_fkey
        FOREIGN KEY (dept_id) REFERENCES branch_departments (dept_id) ON DELETE RESTRICT,
    CONSTRAINT accounting_salary_fiscal_month_check CHECK (fiscal_month >= 1 AND fiscal_month <= 12),
    CONSTRAINT accounting_salary_posting_status_check
        CHECK (posting_status IN ('draft','pending','posted','reversed'))
);

CREATE INDEX idx_accounting_salary_fy_fm      ON accounting_salary (fiscal_year, fiscal_month);
CREATE INDEX idx_accounting_salary_period_cc  ON accounting_salary (pay_period, cost_centre_id);
CREATE INDEX idx_accounting_salary_posting    ON accounting_salary (posting_status);

-- --- ps_payslip_documents ----------------------------------------------------
-- NOTE: employee_id has no documented FK; replicated as-is.
CREATE TABLE ps_payslip_documents (
    payslip_id        uuid          NOT NULL DEFAULT gen_random_uuid(),
    employee_id       uuid          NOT NULL,
    period            varchar(7)    NOT NULL,
    pdf_path          varchar(255),
    gross             numeric(12,2),
    total_deductions  numeric(12,2),
    net               numeric(12,2),
    status            varchar(15)   NOT NULL DEFAULT 'draft',
    published_at      timestamptz,
    created_by        bigint        NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    is_active         boolean       NOT NULL DEFAULT true,
    CONSTRAINT ps_payslip_documents_pkey PRIMARY KEY (payslip_id),
    CONSTRAINT ps_payslip_documents_emp_period_key UNIQUE (employee_id, period),
    CONSTRAINT ps_payslip_documents_status_check CHECK (status IN ('draft','generated','sent'))
);

CREATE INDEX idx_ps_payslip_documents_employee_id ON ps_payslip_documents (employee_id);
CREATE INDEX idx_ps_payslip_documents_period      ON ps_payslip_documents (period);
CREATE INDEX idx_ps_payslip_documents_status      ON ps_payslip_documents (status);

-- --- ps_payslip_access_log ---------------------------------------------------
CREATE TABLE ps_payslip_access_log (
    log_id       uuid        NOT NULL DEFAULT gen_random_uuid(),
    payslip_id   uuid        NOT NULL,
    accessed_by  bigint      NOT NULL,
    action       varchar(15) NOT NULL,
    ip           varchar(45),
    accessed_at  timestamptz NOT NULL,
    CONSTRAINT ps_payslip_access_log_pkey PRIMARY KEY (log_id),
    CONSTRAINT ps_payslip_access_log_payslip_id_fkey
        FOREIGN KEY (payslip_id) REFERENCES ps_payslip_documents (payslip_id) ON DELETE CASCADE,
    CONSTRAINT ps_payslip_access_log_action_check CHECK (action IN ('viewed','downloaded'))
);

CREATE INDEX idx_ps_payslip_access_log_payslip ON ps_payslip_access_log (payslip_id);

-- --- pg_loans ----------------------------------------------------------------
-- NOTE: employee_id has no documented FK; replicated as-is.
CREATE TABLE pg_loans (
    loan_id             uuid          NOT NULL DEFAULT gen_random_uuid(),
    loan_no             varchar(30)   NOT NULL,
    employee_id         uuid          NOT NULL,
    type                varchar(15)   NOT NULL,
    principal_amount    numeric(12,2) NOT NULL,
    installments_count  integer       NOT NULL,
    installment_amount  numeric(12,2) NOT NULL,
    remaining_amount    numeric(12,2) NOT NULL,
    reason              varchar(255),
    status              varchar(20)   NOT NULL DEFAULT 'pending_approval',
    approval_id         uuid,
    start_period        varchar(7)    NOT NULL,
    created_by          bigint        NOT NULL,
    created_at          timestamptz,
    updated_at          timestamptz,
    is_active           boolean       NOT NULL DEFAULT true,
    CONSTRAINT pg_loans_pkey PRIMARY KEY (loan_id),
    CONSTRAINT pg_loans_loan_no_key UNIQUE (loan_no),
    CONSTRAINT pg_loans_status_check
        CHECK (status IN ('pending_approval','approved','active','settled','rejected','cancelled')),
    CONSTRAINT pg_loans_type_check CHECK (type IN ('loan','advance'))
);

CREATE INDEX idx_pg_loans_employee_id ON pg_loans (employee_id);
CREATE INDEX idx_pg_loans_status      ON pg_loans (status);

-- --- pg_loan_schedules -------------------------------------------------------
CREATE TABLE pg_loan_schedules (
    schedule_id  uuid          NOT NULL DEFAULT gen_random_uuid(),
    loan_id      uuid          NOT NULL,
    period       varchar(7)    NOT NULL,
    amount       numeric(12,2) NOT NULL,
    status       varchar(15)   NOT NULL DEFAULT 'scheduled',
    created_at   timestamptz,
    updated_at   timestamptz,
    CONSTRAINT pg_loan_schedules_pkey PRIMARY KEY (schedule_id),
    CONSTRAINT pg_loan_schedules_loan_id_fkey
        FOREIGN KEY (loan_id) REFERENCES pg_loans (loan_id) ON DELETE RESTRICT,
    CONSTRAINT pg_loan_schedules_status_check CHECK (status IN ('scheduled','deducted','skipped'))
);

CREATE INDEX idx_pg_loan_schedules_loan_id ON pg_loan_schedules (loan_id);
CREATE INDEX idx_pg_loan_schedules_period  ON pg_loan_schedules (period);
CREATE INDEX idx_pg_loan_schedules_status  ON pg_loan_schedules (status);

-- --- pg_bonuses --------------------------------------------------------------
-- NOTE: employee_id has no documented FK; replicated as-is.
CREATE TABLE pg_bonuses (
    bonus_id     uuid          NOT NULL DEFAULT gen_random_uuid(),
    employee_id  uuid          NOT NULL,
    type         varchar(20)   NOT NULL,
    amount       numeric(12,2) NOT NULL,
    period       varchar(7)    NOT NULL,
    status       varchar(20)   NOT NULL DEFAULT 'pending_approval',
    approval_id  uuid,
    note         varchar(255),
    created_by   bigint        NOT NULL,
    created_at   timestamptz,
    updated_at   timestamptz,
    is_active    boolean       NOT NULL DEFAULT true,
    CONSTRAINT pg_bonuses_pkey PRIMARY KEY (bonus_id),
    CONSTRAINT pg_bonuses_status_check
        CHECK (status IN ('pending_approval','approved','injected','rejected')),
    CONSTRAINT pg_bonuses_type_check CHECK (type IN ('performance','spot','eid','other'))
);

CREATE INDEX idx_pg_bonuses_employee_id ON pg_bonuses (employee_id);
CREATE INDEX idx_pg_bonuses_period      ON pg_bonuses (period);
CREATE INDEX idx_pg_bonuses_status      ON pg_bonuses (status);

-- --- pg_expense_claims -------------------------------------------------------
-- NOTE: currency_code documented as bare `char` with default 'EGP'; inferred char(3).
CREATE TABLE pg_expense_claims (
    claim_id       uuid          NOT NULL DEFAULT gen_random_uuid(),
    claim_no       varchar(30)   NOT NULL,
    employee_id    uuid          NOT NULL,
    category       varchar(30)   NOT NULL,
    amount         numeric(12,2) NOT NULL,
    currency_code  char(3)       NOT NULL DEFAULT 'EGP',
    incurred_on    date          NOT NULL,
    receipt_path   varchar(255),
    status         varchar(20)   NOT NULL DEFAULT 'pending_approval',
    approval_id    uuid,
    payout_method  varchar(15)   NOT NULL DEFAULT 'payroll',
    created_by     bigint        NOT NULL,
    created_at     timestamptz,
    updated_at     timestamptz,
    is_active      boolean       NOT NULL DEFAULT true,
    CONSTRAINT pg_expense_claims_pkey PRIMARY KEY (claim_id),
    CONSTRAINT pg_expense_claims_claim_no_key UNIQUE (claim_no),
    CONSTRAINT pg_expense_claims_category_check
        CHECK (category IN ('travel','meals','medical','transport','other')),
    CONSTRAINT pg_expense_claims_payout_method_check
        CHECK (payout_method IN ('payroll','petty_cash')),
    CONSTRAINT pg_expense_claims_status_check
        CHECK (status IN ('pending_approval','approved','rejected','paid'))
);

CREATE INDEX idx_pg_expense_claims_employee_id ON pg_expense_claims (employee_id);
CREATE INDEX idx_pg_expense_claims_status      ON pg_expense_claims (status);

-- --- pg_payroll_injections ---------------------------------------------------
CREATE TABLE pg_payroll_injections (
    injection_id  uuid          NOT NULL DEFAULT gen_random_uuid(),
    source_type   varchar(20)   NOT NULL,
    source_id     uuid          NOT NULL,
    employee_id   uuid          NOT NULL,
    period        varchar(7)    NOT NULL,
    direction     varchar(10)   NOT NULL,
    amount        numeric(12,2) NOT NULL,
    status        varchar(15)   NOT NULL DEFAULT 'pending',
    synced_at     timestamptz,
    created_at    timestamptz,
    updated_at    timestamptz,
    is_active     boolean       NOT NULL DEFAULT true,
    CONSTRAINT pg_payroll_injections_pkey PRIMARY KEY (injection_id),
    CONSTRAINT pg_payroll_injections_source_key UNIQUE (source_type, source_id, period),
    CONSTRAINT pg_payroll_injections_direction_check CHECK (direction IN ('deduction','addition')),
    CONSTRAINT pg_payroll_injections_source_type_check CHECK (source_type IN ('loan','expense','bonus')),
    CONSTRAINT pg_payroll_injections_status_check CHECK (status IN ('pending','included','sent'))
);

CREATE INDEX idx_pg_payroll_injections_employee_id ON pg_payroll_injections (employee_id);
CREATE INDEX idx_pg_payroll_injections_period      ON pg_payroll_injections (period);
CREATE INDEX idx_pg_payroll_injections_status      ON pg_payroll_injections (status);
