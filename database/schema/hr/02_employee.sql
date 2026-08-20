-- =============================================================================
-- HR :: Employee core, documents, contracts, operations
-- Derived from schema_split/HR/ Employee.md, EmergencyContacts.md,
--   EmployeeDocuments.md, EmployeeContract.md, EmployeeChangeRequest.md,
--   EmployeeOperations.md
-- =============================================================================

-- --- employees ---------------------------------------------------------------
CREATE TABLE employees (
    employee_id          uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_number      varchar(20)  NOT NULL,
    branch_id            uuid,
    dept_id              uuid,
    position_id          uuid,
    job_grade_id         uuid,
    manager_id           uuid,
    cost_centre_id       uuid,
    first_name           varchar(100) NOT NULL,
    middle_name          varchar(100),
    last_name            varchar(100) NOT NULL,
    first_name_local     varchar(100),
    last_name_local      varchar(100),
    national_id          varchar(50),
    national_id_type     varchar(20),
    passport_number      varchar(50),
    passport_expiry      date,
    date_of_birth        date,
    place_of_birth       varchar(100),
    gender               varchar(10),
    marital_status       varchar(20),
    nationality          varchar(3),
    religion             varchar(50),
    blood_type           varchar(5),
    email_work           varchar(200) NOT NULL,
    email_personal       varchar(200),
    phone_work           varchar(30),
    phone_mobile         varchar(30),
    phone_home           varchar(30),
    address_line1        varchar(200),
    address_line2        varchar(200),
    city                 varchar(100),
    state                varchar(100),
    country              varchar(3),
    postal_code          varchar(20),
    employment_status    varchar(20)  NOT NULL DEFAULT 'active',
    employment_type      varchar(20)  NOT NULL,
    hire_date            date         NOT NULL,
    seniority_date       date,
    confirmation_date    date,
    termination_date     date,
    termination_reason   text,
    termination_type     varchar(30),
    is_active            boolean      NOT NULL DEFAULT true,
    photo_path           varchar(500),
    created_by           bigint,
    created_at           timestamptz,
    updated_at           timestamptz,
    locale               varchar(10),
    theme                varchar(20),
    eligible_for_rehire  boolean,
    CONSTRAINT employees_pkey PRIMARY KEY (employee_id),
    CONSTRAINT employees_email_work_key      UNIQUE (email_work),
    CONSTRAINT employees_employee_number_key UNIQUE (employee_number),
    CONSTRAINT employees_national_id_key     UNIQUE (national_id),
    CONSTRAINT employees_branch_id_fkey
        FOREIGN KEY (branch_id) REFERENCES organization_branches (branch_id) ON DELETE SET NULL,
    CONSTRAINT employees_cost_centre_id_fkey
        FOREIGN KEY (cost_centre_id) REFERENCES cost_centers (cost_centre_id) ON DELETE SET NULL,
    CONSTRAINT employees_dept_id_fkey
        FOREIGN KEY (dept_id) REFERENCES branch_departments (dept_id) ON DELETE SET NULL,
    CONSTRAINT employees_job_grade_id_fkey
        FOREIGN KEY (job_grade_id) REFERENCES job_grades (job_grade_id) ON DELETE SET NULL,
    CONSTRAINT employees_manager_id_fkey
        FOREIGN KEY (manager_id) REFERENCES employees (employee_id) ON DELETE SET NULL,
    CONSTRAINT employees_position_id_fkey
        FOREIGN KEY (position_id) REFERENCES positions (position_id) ON DELETE SET NULL,
    CONSTRAINT employees_blood_type_check
        CHECK (blood_type IS NULL OR blood_type IN ('a+','a-','b+','b-','ab+','ab-','o+','o-')),
    CONSTRAINT employees_gender_check
        CHECK (gender IS NULL OR gender IN ('male','female')),
    CONSTRAINT employees_dob_check
        CHECK (date_of_birth IS NULL OR hire_date > date_of_birth),
    CONSTRAINT employees_marital_status_check
        CHECK (marital_status IS NULL OR marital_status IN ('single','married','divorced','widowed')),
    CONSTRAINT employees_national_id_type_check
        CHECK (national_id_type IS NULL OR national_id_type IN ('national_id','passport','iqama','work_permit','other')),
    CONSTRAINT employees_employment_status_check
        CHECK (employment_status IN ('active','probation','on_leave','suspended','terminated','resigned','retired','inactive')),
    CONSTRAINT employees_termination_date_check
        CHECK (termination_date IS NULL OR termination_date >= hire_date),
    CONSTRAINT employees_termination_type_check
        CHECK (termination_type IS NULL OR termination_type IN ('resignation','termination','retirement','contract_end','death','redundancy')),
    CONSTRAINT employees_employment_type_check
        CHECK (employment_type IN ('full_time','part_time','contract','internship','temporary'))
);

CREATE INDEX idx_employees_branch_id       ON employees (branch_id);
CREATE INDEX idx_employees_date_of_birth   ON employees (date_of_birth);
CREATE INDEX idx_employees_dept_id         ON employees (dept_id);
CREATE INDEX idx_employees_email_work      ON employees (email_work);
CREATE INDEX idx_employees_employee_number ON employees (employee_number);
CREATE INDEX idx_employees_hire_date       ON employees (hire_date);
CREATE INDEX idx_employees_is_active       ON employees (is_active);
CREATE INDEX idx_employees_job_grade_id    ON employees (job_grade_id);
CREATE INDEX idx_employees_manager_id      ON employees (manager_id);
CREATE INDEX idx_employees_national_id     ON employees (national_id);
CREATE INDEX idx_employees_position_id     ON employees (position_id);

-- --- employee_export_jobs ----------------------------------------------------
CREATE TABLE employee_export_jobs (
    export_job_id  uuid         NOT NULL DEFAULT gen_random_uuid(),
    requested_by   bigint       NOT NULL,
    format         varchar(10)  NOT NULL,
    scope          varchar(20)  NOT NULL,
    filename       varchar(150) NOT NULL,
    status         varchar(20)  NOT NULL DEFAULT 'queued',
    file_path      varchar(255),
    error_message  text,
    created_at     timestamptz,
    updated_at     timestamptz,
    is_active      boolean      NOT NULL DEFAULT true,
    CONSTRAINT employee_export_jobs_pkey PRIMARY KEY (export_job_id),
    CONSTRAINT employee_export_jobs_format_check CHECK (format IN ('xlsx','csv','pdf')),
    CONSTRAINT employee_export_jobs_scope_check  CHECK (scope IN ('all','filtered','selected')),
    CONSTRAINT employee_export_jobs_status_check CHECK (status IN ('queued','processing','completed','failed'))
);

-- --- onboarding_task_templates ----------------------------------------------
CREATE TABLE onboarding_task_templates (
    template_id    uuid         NOT NULL DEFAULT gen_random_uuid(),
    day_milestone  integer      NOT NULL,
    title          varchar(150) NOT NULL,
    description    text,
    sort_order     integer      NOT NULL DEFAULT 0,
    is_active      boolean      NOT NULL DEFAULT true,
    created_at     timestamptz,
    updated_at     timestamptz,
    created_by     bigint,
    CONSTRAINT onboarding_task_templates_pkey PRIMARY KEY (template_id),
    CONSTRAINT onboarding_task_templates_day_milestone_check CHECK (day_milestone IN (1, 30, 90))
);

CREATE INDEX idx_onb_templates_day_milestone ON onboarding_task_templates (day_milestone);
CREATE INDEX idx_onb_templates_is_active     ON onboarding_task_templates (is_active);

-- --- onboarding_task_completions ---------------------------------------------
-- NOTE: employee_id has no documented FK in the schema docs; replicated as-is.
CREATE TABLE onboarding_task_completions (
    completion_id  uuid        NOT NULL DEFAULT gen_random_uuid(),
    employee_id    uuid        NOT NULL,
    template_id    uuid        NOT NULL,
    completed_at   timestamptz,
    created_at     timestamptz,
    updated_at     timestamptz,
    is_active      boolean     NOT NULL DEFAULT true,
    CONSTRAINT onboarding_task_completions_pkey PRIMARY KEY (completion_id),
    CONSTRAINT onboarding_task_completions_emp_template_key UNIQUE (employee_id, template_id),
    CONSTRAINT onboarding_task_completions_template_id_fkey
        FOREIGN KEY (template_id) REFERENCES onboarding_task_templates (template_id) ON DELETE CASCADE
);

-- --- emergency_contacts ------------------------------------------------------
CREATE TABLE emergency_contacts (
    contact_id       uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id      uuid         NOT NULL,
    full_name        varchar(255) NOT NULL,
    full_name_local  varchar(255),
    relationship     varchar(100) NOT NULL,
    phone_primary    varchar(30)  NOT NULL,
    phone_secondary  varchar(30),
    email            varchar(255),
    address          text,
    national_id      varchar(50),
    is_primary       boolean      NOT NULL DEFAULT false,
    is_active        boolean      NOT NULL DEFAULT true,
    created_by       bigint,
    created_at       timestamptz,
    updated_at       timestamptz,
    CONSTRAINT emergency_contacts_pkey PRIMARY KEY (contact_id),
    CONSTRAINT emergency_contacts_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE
);

CREATE INDEX idx_emergency_contacts_employee_id ON emergency_contacts (employee_id);

-- --- employee_documents ------------------------------------------------------
CREATE TABLE employee_documents (
    document_id         uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id         uuid         NOT NULL,
    document_type       varchar(40)  NOT NULL,
    document_category   varchar(20)  NOT NULL,
    document_title      varchar(255) NOT NULL,
    document_number     varchar(100),
    issuing_authority   varchar(255),
    issuing_country     varchar(2),
    issue_date          date,
    expiry_date         date,
    alert_days_before   smallint     NOT NULL DEFAULT 30,
    file_path           text,
    file_type           varchar(20),
    file_size_kb        integer,
    is_mandatory        boolean      NOT NULL DEFAULT false,
    is_verified         boolean      NOT NULL DEFAULT false,
    verified_by         bigint,
    verified_date       timestamptz,
    verification_notes  text,
    is_active           boolean      NOT NULL DEFAULT true,
    uploaded_at         timestamptz,
    uploaded_by         bigint,
    created_at          timestamptz,
    updated_at          timestamptz,
    CONSTRAINT employee_documents_pkey PRIMARY KEY (document_id),
    CONSTRAINT employee_documents_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE RESTRICT,
    CONSTRAINT employee_documents_category_check
        CHECK (document_category IN ('identity','legal','educational','professional','medical','internal','other')),
    CONSTRAINT employee_documents_dates_check
        CHECK (expiry_date IS NULL OR issue_date IS NULL OR expiry_date > issue_date),
    CONSTRAINT employee_documents_type_check
        CHECK (document_type IN ('degree','passport','degree_certificate','professional_cert','national_id',
                                 'birth_certificate','work_permit','visa','iban_letter','other')),
    CONSTRAINT employee_documents_file_type_check
        CHECK (file_type IS NULL OR file_type IN ('pdf','jpg','jpeg','png','docx'))
);

CREATE INDEX idx_employee_documents_employee_id      ON employee_documents (employee_id);
CREATE INDEX idx_employee_documents_expiry_date      ON employee_documents (expiry_date);
CREATE INDEX idx_employee_documents_emp_expiry       ON employee_documents (employee_id, expiry_date);
CREATE INDEX idx_employee_documents_document_type    ON employee_documents (document_type);

-- --- employee_contracts ------------------------------------------------------
CREATE TABLE employee_contracts (
    contract_id         uuid        NOT NULL DEFAULT gen_random_uuid(),
    employee_id         uuid        NOT NULL,
    contract_number     varchar(20) NOT NULL,
    contract_type       varchar(30) NOT NULL,
    branch_id           uuid,
    dept_id             uuid,
    position_id         uuid,
    job_grade_id        uuid,
    start_date          date        NOT NULL,
    end_date            date,
    is_open_ended       boolean     NOT NULL DEFAULT false,
    work_location       varchar(255),
    work_schedule       varchar(30),
    weekly_hours        smallint,
    probation_days      smallint,
    probation_end_date  date,
    notice_period_days  smallint,
    notice_period_unit  varchar(10),
    renewal_type        varchar(30),
    renewal_count       smallint    NOT NULL DEFAULT 0,
    contract_status     varchar(30) NOT NULL DEFAULT 'active',
    special_conditions  text,
    file_path           text,
    is_current          boolean     NOT NULL DEFAULT true,
    approved_by         bigint,
    approved_date       date,
    created_by          bigint      NOT NULL,
    created_at          timestamptz,
    updated_at          timestamptz,
    is_active           boolean     NOT NULL DEFAULT true,
    CONSTRAINT employee_contracts_pkey PRIMARY KEY (contract_id),
    CONSTRAINT employee_contracts_contract_number_key UNIQUE (contract_number),
    CONSTRAINT employee_contracts_branch_id_fkey
        FOREIGN KEY (branch_id) REFERENCES organization_branches (branch_id) ON DELETE SET NULL,
    CONSTRAINT employee_contracts_dept_id_fkey
        FOREIGN KEY (dept_id) REFERENCES branch_departments (dept_id) ON DELETE SET NULL,
    CONSTRAINT employee_contracts_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE RESTRICT,
    CONSTRAINT employee_contracts_job_grade_id_fkey
        FOREIGN KEY (job_grade_id) REFERENCES job_grades (job_grade_id) ON DELETE SET NULL,
    CONSTRAINT employee_contracts_position_id_fkey
        FOREIGN KEY (position_id) REFERENCES positions (position_id) ON DELETE SET NULL,
    CONSTRAINT employee_contracts_dates_check CHECK (end_date IS NULL OR end_date > start_date),
    CONSTRAINT employee_contracts_status_check
        CHECK (contract_status IN ('draft','active','expired','terminated','renewed','suspended')),
    CONSTRAINT employee_contracts_type_check
        CHECK (contract_type IN ('permanent','fixed_term','temporary','internship','contract','seasonal','secondment')),
    CONSTRAINT employee_contracts_notice_unit_check
        CHECK (notice_period_unit IS NULL OR notice_period_unit IN ('day','week','month')),
    CONSTRAINT employee_contracts_open_ended_check
        CHECK (NOT is_open_ended OR end_date IS NULL),
    CONSTRAINT employee_contracts_work_schedule_check
        CHECK (work_schedule IS NULL OR work_schedule IN ('standard','shift','flexible','remote','hybrid'))
);

CREATE INDEX idx_employee_contracts_number      ON employee_contracts (contract_number);
CREATE INDEX idx_employee_contracts_employee_id ON employee_contracts (employee_id);
CREATE INDEX idx_employee_contracts_end_date    ON employee_contracts (end_date);
CREATE INDEX idx_employee_contracts_emp_current ON employee_contracts (employee_id, is_current);
CREATE INDEX idx_employee_contracts_status      ON employee_contracts (contract_status);

-- --- employee_change_requests ------------------------------------------------
CREATE TABLE employee_change_requests (
    request_id     uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id    uuid         NOT NULL,
    field_name     varchar(255) NOT NULL,
    old_value      text,
    new_value      text         NOT NULL,
    status         varchar(20)  NOT NULL DEFAULT 'pending',
    requested_at   timestamptz  NOT NULL,
    decided_by     integer,
    decided_at     timestamptz,
    decision_note  text,
    is_active      boolean      NOT NULL DEFAULT true,
    created_at     timestamptz,
    updated_at     timestamptz,
    created_by     bigint,
    CONSTRAINT employee_change_requests_pkey PRIMARY KEY (request_id),
    CONSTRAINT employee_change_requests_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE,
    CONSTRAINT employee_change_requests_status_check
        CHECK (status IN ('pending','approved','rejected'))
);

CREATE INDEX idx_emp_change_requests_emp_status ON employee_change_requests (employee_id, status);
CREATE INDEX idx_emp_change_requests_status     ON employee_change_requests (status);

-- --- employee_operations -----------------------------------------------------
CREATE TABLE employee_operations (
    operation_id           uuid          NOT NULL DEFAULT gen_random_uuid(),
    employee_id            uuid          NOT NULL,
    operation_type         varchar(20)   NOT NULL,
    operation_status       varchar(20)   NOT NULL DEFAULT 'executed',
    effective_date         date          NOT NULL,
    reason                 text,
    changes_applied        jsonb,
    warnings               jsonb,
    next_steps             jsonb,
    final_settlement       jsonb,
    origin_branch_id       uuid,
    origin_dept_id         uuid,
    origin_position_id     uuid,
    origin_job_grade_id    uuid,
    origin_cost_centre_id  uuid,
    origin_manager_id      uuid,
    target_branch_id       uuid,
    target_dept_id         uuid,
    target_position_id     uuid,
    target_job_grade_id    uuid,
    target_cost_centre_id  uuid,
    target_manager_id      uuid,
    termination_type       varchar(30),
    termination_reason     text,
    new_basic_salary       numeric(15,2),
    executed_by            bigint,
    executed_at            timestamptz,
    created_at             timestamptz,
    updated_at             timestamptz,
    notice_period_served   boolean,
    before_state           jsonb,
    reversed_by            bigint,
    reversed_at            timestamptz,
    is_active              boolean       NOT NULL DEFAULT true,
    CONSTRAINT employee_operations_pkey PRIMARY KEY (operation_id),
    CONSTRAINT employee_operations_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE RESTRICT,
    CONSTRAINT employee_operations_termination_type_check
        CHECK (termination_type IS NULL OR termination_type IN ('resignation','termination','retirement','contract_end','death','redundancy')),
    CONSTRAINT employee_operations_status_check
        CHECK (operation_status IN ('preview','executed','failed','reversed')),
    CONSTRAINT employee_operations_type_check
        CHECK (operation_type IN ('transfer','promotion','termination'))
);

CREATE INDEX idx_employee_operations_effective_date ON employee_operations (effective_date DESC);
CREATE INDEX idx_employee_operations_employee_id    ON employee_operations (employee_id);
CREATE INDEX idx_employee_operations_executed_at    ON employee_operations (executed_at DESC);
CREATE INDEX idx_employee_operations_type           ON employee_operations (operation_type);

-- --- eo_settlement_documents -------------------------------------------------
-- NOTE: employee_id has no documented FK; replicated as-is.
CREATE TABLE eo_settlement_documents (
    settlement_id     uuid          NOT NULL DEFAULT gen_random_uuid(),
    employee_id       uuid          NOT NULL,
    pdf_path          varchar(255),
    gratuity          numeric(12,2),
    unused_leave      numeric(12,2),
    pro_rata_salary   numeric(12,2),
    total             numeric(12,2),
    generated_at      timestamptz   NOT NULL,
    created_by        bigint        NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    is_active         boolean       NOT NULL DEFAULT true,
    CONSTRAINT eo_settlement_documents_pkey PRIMARY KEY (settlement_id)
);

CREATE INDEX idx_eo_settlement_documents_employee_id ON eo_settlement_documents (employee_id);

-- --- eo_settlement_access_log ------------------------------------------------
CREATE TABLE eo_settlement_access_log (
    log_id         uuid        NOT NULL DEFAULT gen_random_uuid(),
    settlement_id  uuid        NOT NULL,
    accessed_by    bigint      NOT NULL,
    action         varchar(15) NOT NULL,
    ip             varchar(45),
    accessed_at    timestamptz NOT NULL,
    CONSTRAINT eo_settlement_access_log_pkey PRIMARY KEY (log_id),
    CONSTRAINT eo_settlement_access_log_settlement_id_fkey
        FOREIGN KEY (settlement_id) REFERENCES eo_settlement_documents (settlement_id) ON DELETE CASCADE,
    CONSTRAINT eo_settlement_access_log_action_check CHECK (action IN ('viewed','downloaded'))
);

CREATE INDEX idx_eo_settlement_access_log_settlement ON eo_settlement_access_log (settlement_id);
