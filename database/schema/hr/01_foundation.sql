-- =============================================================================
-- HR :: Foundation (org structure, cost centres, grades, jobs, positions)
-- Derived verbatim from DATABASE_SCHEMA_by_module/schema_split/HR/
--   OrganizationBranch.md, BranchDepartment.md, CostCenter.md,
--   JobGrade.md, JobCatalog.md, Position.md
-- No columns, keys, constraints or defaults were added, removed or redesigned.
-- =============================================================================

-- --- organization_branches ---------------------------------------------------
CREATE TABLE organization_branches (
    branch_id          uuid         NOT NULL DEFAULT gen_random_uuid(),
    branch_code        varchar(20)  NOT NULL,
    branch_name        varchar(200) NOT NULL,
    branch_name_local  varchar(200),
    branch_type        varchar(50)  NOT NULL,
    parent_branch_id   uuid,
    country_code       varchar(2)   NOT NULL,
    city               varchar(100) NOT NULL,
    address_line1      text,
    address_line2      text,
    postal_code        varchar(20),
    phone              varchar(50),
    email              varchar(200),
    tax_id             varchar(100),
    manager_emp_id     uuid,
    is_head_office     boolean      NOT NULL DEFAULT false,
    is_active          boolean      NOT NULL DEFAULT true,
    established_date   date,
    created_by         bigint,
    created_at         timestamptz,
    updated_at         timestamptz,
    CONSTRAINT organization_branches_pkey PRIMARY KEY (branch_id),
    CONSTRAINT organization_branches_branch_code_key UNIQUE (branch_code),
    CONSTRAINT organization_branches_parent_branch_id_fkey
        FOREIGN KEY (parent_branch_id) REFERENCES organization_branches (branch_id) ON DELETE RESTRICT,
    CONSTRAINT organization_branches_branch_type_check
        CHECK (branch_type IN ('head_office','regional_office','branch','warehouse','site'))
);

CREATE INDEX idx_org_branches_is_active       ON organization_branches (is_active);
CREATE INDEX idx_org_branches_country_code    ON organization_branches (country_code);
CREATE INDEX idx_org_branches_is_head_office  ON organization_branches (is_head_office);
CREATE INDEX idx_org_branches_parent          ON organization_branches (parent_branch_id);
CREATE INDEX idx_org_branches_branch_type     ON organization_branches (branch_type);
CREATE INDEX idx_org_branches_branch_code     ON organization_branches (branch_code);

-- --- branch_departments ------------------------------------------------------
CREATE TABLE branch_departments (
    dept_id           uuid         NOT NULL DEFAULT gen_random_uuid(),
    branch_id         uuid         NOT NULL,
    parent_dept_id    uuid,
    dept_code         varchar(20)  NOT NULL,
    dept_name         varchar(200) NOT NULL,
    dept_name_local   varchar(200),
    dept_type         varchar(30)  NOT NULL,
    manager_emp_id    uuid,
    cost_centre_id    uuid,
    headcount_budget  integer      NOT NULL DEFAULT 0,
    headcount_filled  integer      NOT NULL DEFAULT 0,
    effective_date    date,
    is_active         boolean      NOT NULL DEFAULT true,
    created_by        bigint,
    created_at        timestamptz,
    updated_at        timestamptz,
    CONSTRAINT branch_departments_pkey PRIMARY KEY (dept_id),
    CONSTRAINT branch_departments_dept_code_key UNIQUE (dept_code),
    CONSTRAINT branch_departments_branch_id_fkey
        FOREIGN KEY (branch_id) REFERENCES organization_branches (branch_id) ON DELETE RESTRICT,
    CONSTRAINT branch_departments_parent_dept_id_fkey
        FOREIGN KEY (parent_dept_id) REFERENCES branch_departments (dept_id) ON DELETE RESTRICT,
    CONSTRAINT branch_departments_headcount_budget_check CHECK (headcount_budget >= 0),
    CONSTRAINT branch_departments_headcount_filled_check CHECK (headcount_filled >= 0),
    CONSTRAINT branch_departments_dept_type_check
        CHECK (dept_type IN ('operational','administrative','support','technical','management'))
);

CREATE INDEX idx_branch_departments_dept_code   ON branch_departments (dept_code);
CREATE INDEX idx_branch_departments_branch_id   ON branch_departments (branch_id);
CREATE INDEX idx_branch_departments_cc          ON branch_departments (cost_centre_id);
CREATE INDEX idx_branch_departments_is_active   ON branch_departments (is_active);
CREATE INDEX idx_branch_departments_parent      ON branch_departments (parent_dept_id);
CREATE INDEX idx_branch_departments_dept_type   ON branch_departments (dept_type);

-- --- cost_centers ------------------------------------------------------------
-- NOTE: currency_code is documented as bare `char`. A bare `char` is char(1) in
-- PostgreSQL, which cannot hold an ISO-4217 code; length inferred as 3.
CREATE TABLE cost_centers (
    cost_centre_id  uuid           NOT NULL DEFAULT gen_random_uuid(),
    parent_cc_id    uuid,
    branch_id       uuid,
    dept_id         uuid,
    cc_code         varchar(20)    NOT NULL,
    cc_name         varchar(200)   NOT NULL,
    cc_name_local   varchar(200),
    cc_type         varchar(30)    NOT NULL,
    account_code    varchar(50),
    currency_code   char(3)        NOT NULL,
    budget_amount   numeric(15,2)  NOT NULL DEFAULT 0,
    fiscal_year     smallint       NOT NULL,
    is_active       boolean        NOT NULL DEFAULT true,
    created_by      bigint,
    created_at      timestamptz,
    updated_at      timestamptz,
    CONSTRAINT cost_centers_pkey PRIMARY KEY (cost_centre_id),
    CONSTRAINT cost_centers_cc_code_fiscal_year_key UNIQUE (cc_code, fiscal_year),
    CONSTRAINT cost_centers_branch_id_fkey
        FOREIGN KEY (branch_id) REFERENCES organization_branches (branch_id) ON DELETE RESTRICT,
    CONSTRAINT cost_centers_dept_id_fkey
        FOREIGN KEY (dept_id) REFERENCES branch_departments (dept_id) ON DELETE RESTRICT,
    CONSTRAINT cost_centers_parent_cc_id_fkey
        FOREIGN KEY (parent_cc_id) REFERENCES cost_centers (cost_centre_id) ON DELETE RESTRICT,
    CONSTRAINT cost_centers_budget_amount_check CHECK (budget_amount >= 0),
    CONSTRAINT cost_centers_fiscal_year_check CHECK (fiscal_year >= 2000 AND fiscal_year <= 2100),
    CONSTRAINT cost_centers_cc_type_check
        CHECK (cc_type IN ('profit_centre','cost_centre','investment_centre','revenue_centre'))
);

CREATE INDEX idx_cost_centers_branch_id   ON cost_centers (branch_id);
CREATE INDEX idx_cost_centers_cc_type     ON cost_centers (cc_type);
CREATE INDEX idx_cost_centers_dept_id     ON cost_centers (dept_id);
CREATE INDEX idx_cost_centers_fiscal_year ON cost_centers (fiscal_year);
CREATE INDEX idx_cost_centers_is_active   ON cost_centers (is_active);
CREATE INDEX idx_cost_centers_parent      ON cost_centers (parent_cc_id);

-- --- job_grades --------------------------------------------------------------
-- NOTE: currency_code documented as bare `char`; length inferred as 3.
CREATE TABLE job_grades (
    job_grade_id       uuid          NOT NULL DEFAULT gen_random_uuid(),
    grade_code         varchar(20)   NOT NULL,
    grade_name         varchar(200)  NOT NULL,
    grade_name_local   varchar(200),
    grade_level        smallint      NOT NULL,
    grade_category     varchar(30)   NOT NULL,
    min_salary         numeric(15,2) NOT NULL DEFAULT 0,
    mid_salary         numeric(15,2) NOT NULL DEFAULT 0,
    max_salary         numeric(15,2) NOT NULL DEFAULT 0,
    currency_code      char(3)       NOT NULL,
    description        text,
    overtime_eligible  boolean       NOT NULL DEFAULT false,
    annual_leave_days  smallint      NOT NULL DEFAULT 0,
    sick_leave_days    smallint      NOT NULL DEFAULT 0,
    is_active          boolean       NOT NULL DEFAULT true,
    created_by         bigint,
    created_at         timestamptz,
    updated_at         timestamptz,
    CONSTRAINT job_grades_pkey PRIMARY KEY (job_grade_id),
    CONSTRAINT job_grades_grade_code_key UNIQUE (grade_code),
    CONSTRAINT job_grades_grade_category_check
        CHECK (grade_category IN ('executive','management','professional','technical','administrative','operational')),
    CONSTRAINT job_grades_leave_days_check CHECK (annual_leave_days >= 0 AND sick_leave_days >= 0),
    CONSTRAINT job_grades_grade_level_check CHECK (grade_level >= 1),
    CONSTRAINT job_grades_salary_band_check
        CHECK (min_salary >= 0 AND mid_salary >= min_salary AND max_salary >= mid_salary)
);

CREATE INDEX idx_job_grades_category   ON job_grades (grade_category);
CREATE INDEX idx_job_grades_is_active  ON job_grades (is_active);
CREATE INDEX idx_job_grades_level      ON job_grades (grade_level);
CREATE INDEX idx_job_grades_code       ON job_grades (grade_code);

-- --- job_catalog -------------------------------------------------------------
CREATE TABLE job_catalog (
    job_id                   uuid         NOT NULL DEFAULT gen_random_uuid(),
    job_grade_id             uuid,
    job_code                 varchar(30)  NOT NULL,
    job_title                varchar(200) NOT NULL,
    job_title_local          varchar(200),
    job_family               varchar(100),
    job_subfamily            varchar(100),
    job_level                varchar(20)  NOT NULL,
    job_category             varchar(20)  NOT NULL,
    job_description          text,
    qualifications_required  text,
    skills_required          text,
    min_experience_years     smallint     NOT NULL DEFAULT 0,
    requires_approval        boolean      NOT NULL DEFAULT false,
    is_active                boolean      NOT NULL DEFAULT true,
    created_by               bigint,
    created_at               timestamptz,
    updated_at               timestamptz,
    is_internal              boolean      NOT NULL DEFAULT true,
    CONSTRAINT job_catalog_pkey PRIMARY KEY (job_id),
    CONSTRAINT job_catalog_job_code_key UNIQUE (job_code),
    CONSTRAINT job_catalog_job_grade_id_fkey
        FOREIGN KEY (job_grade_id) REFERENCES job_grades (job_grade_id) ON DELETE RESTRICT,
    CONSTRAINT job_catalog_job_category_check
        CHECK (job_category IN ('full_time','part_time','contract','internship','temporary')),
    CONSTRAINT job_catalog_min_experience_check CHECK (min_experience_years >= 0),
    CONSTRAINT job_catalog_job_level_check
        CHECK (job_level IN ('entry','junior','mid','senior','lead','principal','manager','director','vp','c_level'))
);

CREATE INDEX idx_job_catalog_category   ON job_catalog (job_category);
CREATE INDEX idx_job_catalog_family     ON job_catalog (job_family);
CREATE INDEX idx_job_catalog_grade      ON job_catalog (job_grade_id);
CREATE INDEX idx_job_catalog_is_active  ON job_catalog (is_active);
CREATE INDEX idx_job_catalog_level      ON job_catalog (job_level);
CREATE INDEX idx_job_catalog_code       ON job_catalog (job_code);

-- --- positions ---------------------------------------------------------------
CREATE TABLE positions (
    position_id            uuid         NOT NULL DEFAULT gen_random_uuid(),
    position_code          varchar(30)  NOT NULL,
    position_title         varchar(200) NOT NULL,
    position_title_local   varchar(200),
    dept_id                uuid,
    branch_id              uuid,
    job_id                 uuid,
    cost_centre_id         uuid,
    headcount_budget       smallint     NOT NULL DEFAULT 1,
    headcount_filled       smallint     NOT NULL DEFAULT 0,
    position_status        varchar(20)  NOT NULL DEFAULT 'open',
    is_budgeted            boolean      NOT NULL DEFAULT true,
    effective_date         date,
    expiry_date            date,
    is_active              boolean      NOT NULL DEFAULT true,
    created_by             bigint,
    created_at             timestamptz,
    updated_at             timestamptz,
    headcount_vacant       smallint,
    CONSTRAINT positions_pkey PRIMARY KEY (position_id),
    CONSTRAINT positions_position_code_key UNIQUE (position_code),
    CONSTRAINT positions_branch_id_fkey
        FOREIGN KEY (branch_id) REFERENCES organization_branches (branch_id) ON DELETE RESTRICT,
    CONSTRAINT positions_cost_centre_id_fkey
        FOREIGN KEY (cost_centre_id) REFERENCES cost_centers (cost_centre_id) ON DELETE RESTRICT,
    CONSTRAINT positions_dept_id_fkey
        FOREIGN KEY (dept_id) REFERENCES branch_departments (dept_id) ON DELETE RESTRICT,
    CONSTRAINT positions_job_id_fkey
        FOREIGN KEY (job_id) REFERENCES job_catalog (job_id) ON DELETE RESTRICT,
    CONSTRAINT positions_dates_check
        CHECK (expiry_date IS NULL OR effective_date IS NULL OR expiry_date >= effective_date),
    CONSTRAINT positions_headcount_budget_check CHECK (headcount_budget > 0),
    CONSTRAINT positions_headcount_filled_check CHECK (headcount_filled >= 0),
    CONSTRAINT positions_status_check
        CHECK (position_status IN ('open','filled','frozen','closed','on_hold'))
);

CREATE INDEX idx_positions_branch_id  ON positions (branch_id);
CREATE INDEX idx_positions_dept_id    ON positions (dept_id);
CREATE INDEX idx_positions_is_active  ON positions (is_active);
CREATE INDEX idx_positions_job_id     ON positions (job_id);
CREATE INDEX idx_positions_status     ON positions (position_status);
CREATE INDEX idx_positions_code       ON positions (position_code);
