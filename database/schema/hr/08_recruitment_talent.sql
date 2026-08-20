-- =============================================================================
-- HR :: Recruitment + Talent
-- Derived from schema_split/HR/Recruitment.md and Talent.md
-- =============================================================================

-- --- rec_candidates ----------------------------------------------------------
CREATE TABLE rec_candidates (
    candidate_id             uuid         NOT NULL DEFAULT gen_random_uuid(),
    full_name                varchar(150) NOT NULL,
    email                    varchar(150) NOT NULL,
    phone                    varchar(30),
    source                   varchar(30)  NOT NULL,
    applicant_employee_id    uuid,
    referred_by_employee_id  uuid,
    cv_path                  varchar(255),
    current_title            varchar(120),
    total_experience_years   numeric(4,1),
    gdpr_consent_at          timestamptz,
    created_at               timestamptz,
    updated_at               timestamptz,
    is_active                boolean      NOT NULL DEFAULT true,
    created_by               bigint,
    CONSTRAINT rec_candidates_pkey PRIMARY KEY (candidate_id),
    CONSTRAINT rec_candidates_source_check
        CHECK (source IN ('internal','referral','portal','agency','linkedin','walk_in'))
);

CREATE INDEX idx_rec_candidates_email ON rec_candidates (email);

-- --- rec_job_requisitions ----------------------------------------------------
CREATE TABLE rec_job_requisitions (
    requisition_id     uuid         NOT NULL DEFAULT gen_random_uuid(),
    requisition_no     varchar(30)  NOT NULL,
    job_id             uuid         NOT NULL,
    dept_id            uuid,
    hiring_manager_id  uuid,
    headcount          integer      NOT NULL,
    filled_count       integer      NOT NULL DEFAULT 0,
    status             varchar(20)  NOT NULL DEFAULT 'draft',
    visibility         varchar(15)  NOT NULL DEFAULT 'external',
    location_label     varchar(120),
    employment_type    varchar(20),
    approval_id        uuid,
    opened_at          timestamptz,
    closed_at          timestamptz,
    created_by         bigint       NOT NULL,
    created_at         timestamptz,
    updated_at         timestamptz,
    is_active          boolean      NOT NULL DEFAULT true,
    CONSTRAINT rec_job_requisitions_pkey PRIMARY KEY (requisition_id),
    CONSTRAINT rec_job_requisitions_requisition_no_key UNIQUE (requisition_no),
    CONSTRAINT rec_job_requisitions_dept_id_fkey
        FOREIGN KEY (dept_id) REFERENCES branch_departments (dept_id) ON DELETE SET NULL,
    CONSTRAINT rec_job_requisitions_job_id_fkey
        FOREIGN KEY (job_id) REFERENCES job_catalog (job_id) ON DELETE RESTRICT,
    CONSTRAINT rec_job_requisitions_employment_type_check
        CHECK (employment_type IS NULL OR employment_type IN ('full_time','part_time','contract','internship','temporary')),
    CONSTRAINT rec_job_requisitions_status_check
        CHECK (status IN ('draft','pending_approval','open','on_hold','closed','cancelled')),
    CONSTRAINT rec_job_requisitions_visibility_check
        CHECK (visibility IN ('internal','external','both'))
);

CREATE INDEX idx_rec_job_requisitions_status     ON rec_job_requisitions (status);
CREATE INDEX idx_rec_job_requisitions_visibility ON rec_job_requisitions (visibility);

-- --- rec_pipeline_stages -----------------------------------------------------
CREATE TABLE rec_pipeline_stages (
    stage_id        uuid        NOT NULL DEFAULT gen_random_uuid(),
    requisition_id  uuid,
    name_ar         varchar(60) NOT NULL,
    name_en         varchar(60) NOT NULL,
    sequence_order  integer     NOT NULL,
    stage_type      varchar(20) NOT NULL,
    is_terminal     boolean     NOT NULL DEFAULT false,
    created_at      timestamptz,
    updated_at      timestamptz,
    is_active       boolean     NOT NULL DEFAULT true,
    created_by      bigint,
    CONSTRAINT rec_pipeline_stages_pkey PRIMARY KEY (stage_id),
    CONSTRAINT rec_pipeline_stages_requisition_id_fkey
        FOREIGN KEY (requisition_id) REFERENCES rec_job_requisitions (requisition_id) ON DELETE CASCADE,
    CONSTRAINT rec_pipeline_stages_stage_type_check
        CHECK (stage_type IN ('screening','interview','assessment','offer','hired','rejected'))
);

CREATE INDEX idx_rec_pipeline_stages_requisition ON rec_pipeline_stages (requisition_id);

-- --- rec_applications --------------------------------------------------------
CREATE TABLE rec_applications (
    application_id     uuid         NOT NULL DEFAULT gen_random_uuid(),
    requisition_id     uuid         NOT NULL,
    candidate_id       uuid         NOT NULL,
    current_stage_id   uuid         NOT NULL,
    status             varchar(20)  NOT NULL DEFAULT 'active',
    rejection_reason   varchar(255),
    score              numeric(4,1),
    applied_at         timestamptz  NOT NULL,
    created_at         timestamptz,
    updated_at         timestamptz,
    cover_note         text,
    is_active          boolean      NOT NULL DEFAULT true,
    created_by         bigint,
    CONSTRAINT rec_applications_pkey PRIMARY KEY (application_id),
    CONSTRAINT rec_applications_req_candidate_key UNIQUE (requisition_id, candidate_id),
    CONSTRAINT rec_applications_candidate_id_fkey
        FOREIGN KEY (candidate_id) REFERENCES rec_candidates (candidate_id) ON DELETE RESTRICT,
    CONSTRAINT rec_applications_current_stage_id_fkey
        FOREIGN KEY (current_stage_id) REFERENCES rec_pipeline_stages (stage_id) ON DELETE RESTRICT,
    CONSTRAINT rec_applications_requisition_id_fkey
        FOREIGN KEY (requisition_id) REFERENCES rec_job_requisitions (requisition_id) ON DELETE RESTRICT,
    CONSTRAINT rec_applications_status_check
        CHECK (status IN ('active','rejected','withdrawn','hired'))
);

CREATE INDEX idx_rec_applications_status ON rec_applications (status);

-- --- rec_interviews ----------------------------------------------------------
CREATE TABLE rec_interviews (
    interview_id    uuid        NOT NULL DEFAULT gen_random_uuid(),
    application_id  uuid        NOT NULL,
    stage_id        uuid        NOT NULL,
    interviewer_id  uuid,
    scheduled_at    timestamptz NOT NULL,
    mode            varchar(15) NOT NULL,
    status          varchar(15) NOT NULL DEFAULT 'scheduled',
    feedback        text,
    rating          integer,
    created_at      timestamptz,
    updated_at      timestamptz,
    is_active       boolean     NOT NULL DEFAULT true,
    created_by      bigint,
    CONSTRAINT rec_interviews_pkey PRIMARY KEY (interview_id),
    CONSTRAINT rec_interviews_application_id_fkey
        FOREIGN KEY (application_id) REFERENCES rec_applications (application_id) ON DELETE RESTRICT,
    CONSTRAINT rec_interviews_stage_id_fkey
        FOREIGN KEY (stage_id) REFERENCES rec_pipeline_stages (stage_id) ON DELETE RESTRICT,
    CONSTRAINT rec_interviews_mode_check CHECK (mode IN ('onsite','online','phone')),
    CONSTRAINT rec_interviews_rating_check CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
    CONSTRAINT rec_interviews_status_check CHECK (status IN ('scheduled','done','no_show','cancelled'))
);

CREATE INDEX idx_rec_interviews_application ON rec_interviews (application_id);

-- --- rec_offers --------------------------------------------------------------
-- NOTE: currency_code documented as bare `char` with default 'EGP'; inferred char(3).
CREATE TABLE rec_offers (
    offer_id           uuid          NOT NULL DEFAULT gen_random_uuid(),
    application_id     uuid          NOT NULL,
    offered_grade_id   uuid,
    gross_salary       numeric(12,2) NOT NULL,
    currency_code      char(3)       NOT NULL DEFAULT 'EGP',
    start_date         date          NOT NULL,
    status             varchar(20)   NOT NULL DEFAULT 'draft',
    approval_id        uuid,
    expires_at         timestamptz,
    accepted_at        timestamptz,
    created_at         timestamptz,
    updated_at         timestamptz,
    is_active          boolean       NOT NULL DEFAULT true,
    created_by         bigint,
    CONSTRAINT rec_offers_pkey PRIMARY KEY (offer_id),
    CONSTRAINT rec_offers_application_id_key UNIQUE (application_id),
    CONSTRAINT rec_offers_application_id_fkey
        FOREIGN KEY (application_id) REFERENCES rec_applications (application_id) ON DELETE RESTRICT,
    CONSTRAINT rec_offers_offered_grade_id_fkey
        FOREIGN KEY (offered_grade_id) REFERENCES job_grades (job_grade_id) ON DELETE RESTRICT,
    CONSTRAINT rec_offers_status_check
        CHECK (status IN ('draft','pending_approval','approved','sent','accepted','declined','expired'))
);

CREATE INDEX idx_rec_offers_status ON rec_offers (status);

-- --- tal_career_paths --------------------------------------------------------
CREATE TABLE tal_career_paths (
    path_id                 uuid        NOT NULL DEFAULT gen_random_uuid(),
    from_grade_id           uuid        NOT NULL,
    to_grade_id             uuid        NOT NULL,
    min_tenure_months       integer     NOT NULL,
    required_competencies   jsonb,
    created_at              timestamptz,
    updated_at              timestamptz,
    is_active               boolean     NOT NULL DEFAULT true,
    created_by              bigint,
    CONSTRAINT tal_career_paths_pkey PRIMARY KEY (path_id),
    CONSTRAINT tal_career_paths_from_grade_id_fkey
        FOREIGN KEY (from_grade_id) REFERENCES job_grades (job_grade_id) ON DELETE RESTRICT,
    CONSTRAINT tal_career_paths_to_grade_id_fkey
        FOREIGN KEY (to_grade_id) REFERENCES job_grades (job_grade_id) ON DELETE RESTRICT
);

CREATE INDEX idx_tal_career_paths_from_grade ON tal_career_paths (from_grade_id);

-- --- tal_promotion_criteria --------------------------------------------------
CREATE TABLE tal_promotion_criteria (
    criterion_id    uuid         NOT NULL DEFAULT gen_random_uuid(),
    career_path_id  uuid         NOT NULL,
    name            varchar(255) NOT NULL,
    weight          integer      NOT NULL DEFAULT 1,
    sort_order      integer      NOT NULL DEFAULT 0,
    created_at      timestamptz,
    updated_at      timestamptz,
    is_active       boolean      NOT NULL DEFAULT true,
    created_by      bigint,
    CONSTRAINT tal_promotion_criteria_pkey PRIMARY KEY (criterion_id),
    CONSTRAINT tal_promotion_criteria_career_path_id_fkey
        FOREIGN KEY (career_path_id) REFERENCES tal_career_paths (path_id) ON DELETE CASCADE
);

CREATE INDEX idx_tal_promotion_criteria_path ON tal_promotion_criteria (career_path_id);

-- --- tal_employee_criterion_progress -----------------------------------------
-- NOTE: employee_id has no documented FK; replicated as-is.
CREATE TABLE tal_employee_criterion_progress (
    progress_id       uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id       uuid         NOT NULL,
    criterion_id      uuid         NOT NULL,
    state             varchar(20)  NOT NULL,
    progress_percent  numeric(5,2) NOT NULL DEFAULT 0,
    gaps_count        integer      NOT NULL DEFAULT 0,
    notes             text,
    updated_by        bigint       NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    is_active         boolean      NOT NULL DEFAULT true,
    created_by        bigint,
    CONSTRAINT tal_employee_criterion_progress_pkey PRIMARY KEY (progress_id),
    CONSTRAINT tal_emp_criterion_progress_emp_criterion_key UNIQUE (employee_id, criterion_id),
    CONSTRAINT tal_employee_criterion_progress_criterion_id_fkey
        FOREIGN KEY (criterion_id) REFERENCES tal_promotion_criteria (criterion_id) ON DELETE CASCADE,
    CONSTRAINT tal_employee_criterion_progress_state_check
        CHECK (state IN ('met','in_progress'))
);

CREATE INDEX idx_tal_emp_criterion_progress_emp ON tal_employee_criterion_progress (employee_id);

-- --- tal_promotion_cases -----------------------------------------------------
CREATE TABLE tal_promotion_cases (
    case_id             uuid          NOT NULL DEFAULT gen_random_uuid(),
    case_no             varchar(30)   NOT NULL,
    employee_id         uuid          NOT NULL,
    current_grade_id    uuid,
    proposed_grade_id   uuid          NOT NULL,
    target_position_id  uuid,
    current_salary      numeric(12,2),
    proposed_salary     numeric(12,2) NOT NULL,
    effective_date      date          NOT NULL,
    justification       text,
    status              varchar(20)   NOT NULL DEFAULT 'draft',
    approval_id         uuid,
    applied_at          timestamptz,
    created_by          bigint        NOT NULL,
    created_at          timestamptz,
    updated_at          timestamptz,
    is_active           boolean       NOT NULL DEFAULT true,
    CONSTRAINT tal_promotion_cases_pkey PRIMARY KEY (case_id),
    CONSTRAINT tal_promotion_cases_case_no_key UNIQUE (case_no),
    CONSTRAINT tal_promotion_cases_proposed_grade_id_fkey
        FOREIGN KEY (proposed_grade_id) REFERENCES job_grades (job_grade_id) ON DELETE RESTRICT,
    CONSTRAINT tal_promotion_cases_status_check
        CHECK (status IN ('draft','pending_approval','approved','applied','rejected'))
);

CREATE INDEX idx_tal_promotion_cases_employee ON tal_promotion_cases (employee_id);
CREATE INDEX idx_tal_promotion_cases_status   ON tal_promotion_cases (status);

-- --- tal_promotion_nominations -----------------------------------------------
CREATE TABLE tal_promotion_nominations (
    nomination_id      uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id        uuid         NOT NULL,
    nominator_user_id  bigint       NOT NULL,
    role_label         varchar(255),
    quote              text,
    nominated_at       date         NOT NULL,
    created_at         timestamptz,
    updated_at         timestamptz,
    is_active          boolean      NOT NULL DEFAULT true,
    created_by         bigint,
    CONSTRAINT tal_promotion_nominations_pkey PRIMARY KEY (nomination_id)
);

CREATE INDEX idx_tal_promotion_nominations_emp ON tal_promotion_nominations (employee_id);

-- --- tal_role_highlights -----------------------------------------------------
CREATE TABLE tal_role_highlights (
    highlight_id  uuid         NOT NULL DEFAULT gen_random_uuid(),
    operation_id  uuid         NOT NULL,
    highlight     varchar(255) NOT NULL,
    sort_order    integer      NOT NULL DEFAULT 0,
    created_at    timestamptz,
    updated_at    timestamptz,
    is_active     boolean      NOT NULL DEFAULT true,
    created_by    bigint,
    CONSTRAINT tal_role_highlights_pkey PRIMARY KEY (highlight_id),
    CONSTRAINT tal_role_highlights_operation_id_fkey
        FOREIGN KEY (operation_id) REFERENCES employee_operations (operation_id) ON DELETE CASCADE
);

CREATE INDEX idx_tal_role_highlights_operation ON tal_role_highlights (operation_id);

-- --- tal_succession_candidates -----------------------------------------------
CREATE TABLE tal_succession_candidates (
    succession_id         uuid        NOT NULL DEFAULT gen_random_uuid(),
    position_id           uuid        NOT NULL,
    candidate_employee_id uuid        NOT NULL,
    readiness             varchar(20) NOT NULL,
    rank                  integer     NOT NULL,
    notes                 text,
    created_at            timestamptz,
    updated_at            timestamptz,
    is_active             boolean     NOT NULL DEFAULT true,
    created_by            bigint,
    CONSTRAINT tal_succession_candidates_pkey PRIMARY KEY (succession_id),
    CONSTRAINT tal_succession_candidates_position_id_fkey
        FOREIGN KEY (position_id) REFERENCES positions (position_id) ON DELETE RESTRICT,
    CONSTRAINT tal_succession_candidates_readiness_check
        CHECK (readiness IN ('ready_now','1_2_years','long_term'))
);

CREATE INDEX idx_tal_succession_candidates_position ON tal_succession_candidates (position_id);
