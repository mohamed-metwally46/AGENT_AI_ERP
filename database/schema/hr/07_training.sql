-- =============================================================================
-- HR :: Training, skills, certifications
-- Derived from schema_split/HR/Training.md
-- =============================================================================

-- --- skills ------------------------------------------------------------------
CREATE TABLE skills (
    skill_id          uuid         NOT NULL DEFAULT gen_random_uuid(),
    skill_code        varchar(30)  NOT NULL,
    skill_name        varchar(150) NOT NULL,
    skill_name_local  varchar(150),
    category          varchar(50)  NOT NULL,
    description       text,
    is_active         boolean      NOT NULL DEFAULT true,
    created_by        bigint       NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    CONSTRAINT skills_pkey PRIMARY KEY (skill_id),
    CONSTRAINT skills_skill_code_key UNIQUE (skill_code)
);

CREATE INDEX idx_skills_category  ON skills (category);
CREATE INDEX idx_skills_is_active ON skills (is_active);

-- --- employee_skills ---------------------------------------------------------
-- NOTE: employee_id has no documented FK; replicated as-is.
CREATE TABLE employee_skills (
    emp_skill_id   uuid        NOT NULL DEFAULT gen_random_uuid(),
    employee_id    uuid        NOT NULL,
    skill_id       uuid        NOT NULL,
    current_level  smallint    NOT NULL,
    target_level   smallint,
    assessed_by    uuid,
    assessed_at    timestamptz,
    source         varchar(20) NOT NULL DEFAULT 'self',
    created_at     timestamptz,
    updated_at     timestamptz,
    is_active      boolean     NOT NULL DEFAULT true,
    CONSTRAINT employee_skills_pkey PRIMARY KEY (emp_skill_id),
    CONSTRAINT employee_skills_emp_skill_key UNIQUE (employee_id, skill_id),
    CONSTRAINT employee_skills_skill_id_fkey
        FOREIGN KEY (skill_id) REFERENCES skills (skill_id) ON DELETE RESTRICT,
    CONSTRAINT employee_skills_current_level_check CHECK (current_level >= 1 AND current_level <= 5),
    CONSTRAINT employee_skills_source_check
        CHECK (source IN ('self','manager','assessment','certification','training')),
    CONSTRAINT employee_skills_target_level_check
        CHECK (target_level IS NULL OR (target_level >= 1 AND target_level <= 5))
);

CREATE INDEX idx_employee_skills_employee    ON employee_skills (employee_id);
CREATE INDEX idx_employee_skills_skill_level ON employee_skills (skill_id, current_level);

-- --- role_skill_requirements -------------------------------------------------
-- NOTE: job_grade_id has no documented FK; replicated as-is.
CREATE TABLE role_skill_requirements (
    req_id          uuid        NOT NULL DEFAULT gen_random_uuid(),
    job_grade_id    uuid        NOT NULL,
    skill_id        uuid        NOT NULL,
    required_level  smallint    NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active       boolean     NOT NULL DEFAULT true,
    created_by      bigint,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT role_skill_requirements_pkey PRIMARY KEY (req_id),
    CONSTRAINT role_skill_requirements_grade_skill_key UNIQUE (job_grade_id, skill_id),
    CONSTRAINT role_skill_requirements_skill_id_fkey
        FOREIGN KEY (skill_id) REFERENCES skills (skill_id) ON DELETE RESTRICT,
    CONSTRAINT role_skill_requirements_level_check CHECK (required_level >= 1 AND required_level <= 5)
);

CREATE INDEX idx_role_skill_requirements_grade ON role_skill_requirements (job_grade_id);

-- --- training_courses --------------------------------------------------------
CREATE TABLE training_courses (
    course_id          uuid          NOT NULL DEFAULT gen_random_uuid(),
    course_code        varchar(30)   NOT NULL,
    course_name        varchar(200)  NOT NULL,
    course_name_local  varchar(200),
    category           varchar(50)   NOT NULL,
    delivery_mode      varchar(30)   NOT NULL,
    level              varchar(20)   NOT NULL,
    duration_hours     numeric(6,2)  NOT NULL,
    provider           varchar(200),
    cost_per_seat      numeric(12,2) NOT NULL DEFAULT 0,
    currency_code      varchar(3)    NOT NULL DEFAULT 'EGP',
    passing_score      numeric(5,2),
    validity_months    smallint,
    is_mandatory       boolean       NOT NULL DEFAULT false,
    is_active          boolean       NOT NULL DEFAULT true,
    created_by         bigint        NOT NULL,
    created_at         timestamptz,
    updated_at         timestamptz,
    CONSTRAINT training_courses_pkey PRIMARY KEY (course_id),
    CONSTRAINT training_courses_course_code_key UNIQUE (course_code),
    CONSTRAINT training_courses_category_check
        CHECK (category IN ('technical','soft_skills','compliance','leadership','onboarding','safety','product')),
    CONSTRAINT training_courses_cost_check CHECK (cost_per_seat >= 0),
    CONSTRAINT training_courses_delivery_mode_check
        CHECK (delivery_mode IN ('classroom','e_learning','blended','online','hybrid')),
    CONSTRAINT training_courses_level_check
        CHECK (level IN ('beginner','intermediate','advanced','expert')),
    CONSTRAINT training_courses_passing_score_check
        CHECK (passing_score IS NULL OR (passing_score >= 0 AND passing_score <= 100))
);

CREATE INDEX idx_training_courses_cat_active ON training_courses (category, is_active);

-- --- course_modules ----------------------------------------------------------
CREATE TABLE course_modules (
    module_id         uuid         NOT NULL DEFAULT gen_random_uuid(),
    course_id         uuid         NOT NULL,
    sequence_order    smallint     NOT NULL,
    title             varchar(200) NOT NULL,
    description       text,
    material_url      varchar(500),
    duration_minutes  integer,
    created_at        timestamptz  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active         boolean      NOT NULL DEFAULT true,
    created_by        bigint,
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT course_modules_pkey PRIMARY KEY (module_id),
    CONSTRAINT course_modules_course_id_fkey
        FOREIGN KEY (course_id) REFERENCES training_courses (course_id) ON DELETE CASCADE
);

CREATE INDEX idx_course_modules_course_seq ON course_modules (course_id, sequence_order);

-- --- course_prerequisites ----------------------------------------------------
CREATE TABLE course_prerequisites (
    prereq_id               uuid        NOT NULL DEFAULT gen_random_uuid(),
    course_id               uuid        NOT NULL,
    prerequisite_course_id  uuid        NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active               boolean     NOT NULL DEFAULT true,
    created_by              bigint,
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT course_prerequisites_pkey PRIMARY KEY (prereq_id),
    CONSTRAINT course_prerequisites_course_prereq_key UNIQUE (course_id, prerequisite_course_id),
    CONSTRAINT course_prerequisites_course_id_fkey
        FOREIGN KEY (course_id) REFERENCES training_courses (course_id) ON DELETE CASCADE,
    CONSTRAINT course_prerequisites_prerequisite_course_id_fkey
        FOREIGN KEY (prerequisite_course_id) REFERENCES training_courses (course_id) ON DELETE CASCADE
);

-- --- training_programs -------------------------------------------------------
CREATE TABLE training_programs (
    program_id            uuid          NOT NULL DEFAULT gen_random_uuid(),
    program_code          varchar(30)   NOT NULL,
    program_name          varchar(200)  NOT NULL,
    program_name_local    varchar(200),
    category              varchar(50)   NOT NULL,
    description           text,
    status                varchar(20)   NOT NULL DEFAULT 'draft',
    provider              varchar(200),
    owner_emp_id          uuid,
    target_audience       varchar(300),
    total_duration_hours  numeric(8,2)  NOT NULL DEFAULT 0,
    cost_per_seat         numeric(12,2) NOT NULL DEFAULT 0,
    currency_code         varchar(3)    NOT NULL DEFAULT 'EGP',
    max_cohort_size       integer,
    start_date            date,
    end_date              date,
    is_active             boolean       NOT NULL DEFAULT true,
    created_by            bigint        NOT NULL,
    created_at            timestamptz,
    updated_at            timestamptz,
    CONSTRAINT training_programs_pkey PRIMARY KEY (program_id),
    CONSTRAINT training_programs_program_code_key UNIQUE (program_code),
    CONSTRAINT training_programs_category_check
        CHECK (category IN ('technical','soft_skills','compliance','leadership','onboarding','safety','product')),
    CONSTRAINT training_programs_cost_check CHECK (cost_per_seat >= 0),
    CONSTRAINT training_programs_status_check
        CHECK (status IN ('draft','active','paused','completed','archived'))
);

CREATE INDEX idx_training_programs_cat_status ON training_programs (category, status);
CREATE INDEX idx_training_programs_owner      ON training_programs (owner_emp_id);

-- --- program_cohorts ---------------------------------------------------------
CREATE TABLE program_cohorts (
    cohort_id       uuid         NOT NULL DEFAULT gen_random_uuid(),
    program_id      uuid         NOT NULL,
    cohort_name     varchar(150) NOT NULL,
    start_date      date,
    end_date        date,
    capacity        integer,
    enrolled_count  integer      NOT NULL DEFAULT 0,
    status          varchar(20)  NOT NULL DEFAULT 'planned',
    created_at      timestamptz  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active       boolean      NOT NULL DEFAULT true,
    created_by      bigint,
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT program_cohorts_pkey PRIMARY KEY (cohort_id),
    CONSTRAINT program_cohorts_program_name_key UNIQUE (program_id, cohort_name),
    CONSTRAINT program_cohorts_program_id_fkey
        FOREIGN KEY (program_id) REFERENCES training_programs (program_id) ON DELETE CASCADE,
    CONSTRAINT program_cohorts_status_check
        CHECK (status IN ('planned','open','full','in_progress','completed','cancelled'))
);

CREATE INDEX idx_program_cohorts_program_status ON program_cohorts (program_id, status);

-- --- program_courses ---------------------------------------------------------
CREATE TABLE program_courses (
    program_course_id  uuid        NOT NULL DEFAULT gen_random_uuid(),
    program_id         uuid        NOT NULL,
    course_id          uuid        NOT NULL,
    sequence_order     smallint    NOT NULL DEFAULT 1,
    is_mandatory       boolean     NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active          boolean     NOT NULL DEFAULT true,
    created_by         bigint,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT program_courses_pkey PRIMARY KEY (program_course_id),
    CONSTRAINT program_courses_program_course_key UNIQUE (program_id, course_id),
    CONSTRAINT program_courses_course_id_fkey
        FOREIGN KEY (course_id) REFERENCES training_courses (course_id) ON DELETE RESTRICT,
    CONSTRAINT program_courses_program_id_fkey
        FOREIGN KEY (program_id) REFERENCES training_programs (program_id) ON DELETE CASCADE
);

CREATE INDEX idx_program_courses_program_seq ON program_courses (program_id, sequence_order);

-- --- program_enrollments -----------------------------------------------------
-- NOTE: employee_id has no documented FK; replicated as-is.
CREATE TABLE program_enrollments (
    enrollment_id        uuid         NOT NULL DEFAULT gen_random_uuid(),
    program_id           uuid         NOT NULL,
    cohort_id            uuid,
    employee_id          uuid         NOT NULL,
    status               varchar(20)  NOT NULL DEFAULT 'enrolled',
    enrolled_at          timestamptz,
    completion_pct       numeric(5,2) NOT NULL DEFAULT 0,
    completed_at         timestamptz,
    approval_request_id  uuid,
    created_at           timestamptz,
    updated_at           timestamptz,
    is_active            boolean      NOT NULL DEFAULT true,
    CONSTRAINT program_enrollments_pkey PRIMARY KEY (enrollment_id),
    CONSTRAINT program_enrollments_program_emp_key UNIQUE (program_id, employee_id),
    CONSTRAINT program_enrollments_cohort_id_fkey
        FOREIGN KEY (cohort_id) REFERENCES program_cohorts (cohort_id) ON DELETE SET NULL,
    CONSTRAINT program_enrollments_program_id_fkey
        FOREIGN KEY (program_id) REFERENCES training_programs (program_id) ON DELETE RESTRICT,
    CONSTRAINT program_enrollments_completion_check
        CHECK (completion_pct >= 0 AND completion_pct <= 100),
    CONSTRAINT program_enrollments_status_check
        CHECK (status IN ('enrolled','in_progress','completed','dropped','withdrawn'))
);

CREATE INDEX idx_program_enrollments_emp_status     ON program_enrollments (employee_id, status);
CREATE INDEX idx_program_enrollments_program_status ON program_enrollments (program_id, status);

-- --- training_sessions -------------------------------------------------------
CREATE TABLE training_sessions (
    session_id        uuid         NOT NULL DEFAULT gen_random_uuid(),
    course_id         uuid         NOT NULL,
    cohort_id         uuid,
    start_datetime    timestamptz  NOT NULL,
    end_datetime      timestamptz  NOT NULL,
    trainer_emp_id    uuid,
    external_trainer  varchar(200),
    venue             varchar(300),
    delivery_mode     varchar(30)  NOT NULL,
    max_seats         integer      NOT NULL,
    enrolled_count    integer      NOT NULL DEFAULT 0,
    waitlist_count    integer      NOT NULL DEFAULT 0,
    status            varchar(20)  NOT NULL DEFAULT 'scheduled',
    created_by        bigint       NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    is_active         boolean      NOT NULL DEFAULT true,
    CONSTRAINT training_sessions_pkey PRIMARY KEY (session_id),
    CONSTRAINT training_sessions_cohort_id_fkey
        FOREIGN KEY (cohort_id) REFERENCES program_cohorts (cohort_id) ON DELETE SET NULL,
    CONSTRAINT training_sessions_course_id_fkey
        FOREIGN KEY (course_id) REFERENCES training_courses (course_id) ON DELETE RESTRICT,
    CONSTRAINT training_sessions_datetime_check CHECK (end_datetime > start_datetime),
    CONSTRAINT training_sessions_delivery_mode_check
        CHECK (delivery_mode IN ('classroom','e_learning','blended','online','hybrid')),
    CONSTRAINT training_sessions_max_seats_check CHECK (max_seats > 0),
    CONSTRAINT training_sessions_status_check
        CHECK (status IN ('scheduled','open','full','in_progress','completed','cancelled'))
);

CREATE INDEX idx_training_sessions_course_start ON training_sessions (course_id, start_datetime);
CREATE INDEX idx_training_sessions_status       ON training_sessions (status);

-- --- session_enrollments -----------------------------------------------------
CREATE TABLE session_enrollments (
    enrollment_id        uuid        NOT NULL DEFAULT gen_random_uuid(),
    session_id           uuid        NOT NULL,
    employee_id          uuid        NOT NULL,
    status               varchar(20) NOT NULL DEFAULT 'requested',
    waitlist_position    integer,
    approval_request_id  uuid,
    enrolled_at          timestamptz,
    created_at           timestamptz,
    updated_at           timestamptz,
    is_active            boolean     NOT NULL DEFAULT true,
    CONSTRAINT session_enrollments_pkey PRIMARY KEY (enrollment_id),
    CONSTRAINT session_enrollments_session_emp_key UNIQUE (session_id, employee_id),
    CONSTRAINT session_enrollments_session_id_fkey
        FOREIGN KEY (session_id) REFERENCES training_sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT session_enrollments_status_check
        CHECK (status IN ('requested','approved','enrolled','waitlisted','attended','no_show','cancelled'))
);

CREATE INDEX idx_session_enrollments_emp_status ON session_enrollments (employee_id, status);

-- --- session_attendances -----------------------------------------------------
CREATE TABLE session_attendances (
    attendance_id  uuid         NOT NULL DEFAULT gen_random_uuid(),
    session_id     uuid         NOT NULL,
    employee_id    uuid         NOT NULL,
    attended       boolean      NOT NULL DEFAULT false,
    score          numeric(5,2),
    result         varchar(10)  NOT NULL DEFAULT 'pending',
    marked_by      uuid,
    marked_at      timestamptz,
    created_at     timestamptz  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active      boolean      NOT NULL DEFAULT true,
    created_by     bigint,
    updated_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT session_attendances_pkey PRIMARY KEY (attendance_id),
    CONSTRAINT session_attendances_session_emp_key UNIQUE (session_id, employee_id),
    CONSTRAINT session_attendances_session_id_fkey
        FOREIGN KEY (session_id) REFERENCES training_sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT session_attendances_result_check CHECK (result IN ('pass','fail','pending')),
    CONSTRAINT session_attendances_score_check
        CHECK (score IS NULL OR (score >= 0 AND score <= 100))
);

CREATE INDEX idx_session_attendances_employee ON session_attendances (employee_id);

-- --- training_feedbacks ------------------------------------------------------
CREATE TABLE training_feedbacks (
    feedback_id        uuid        NOT NULL DEFAULT gen_random_uuid(),
    session_id         uuid        NOT NULL,
    employee_id        uuid        NOT NULL,
    content_rating     smallint    NOT NULL,
    trainer_rating     smallint    NOT NULL,
    relevance_rating   smallint    NOT NULL,
    would_recommend    boolean     NOT NULL DEFAULT true,
    comments           text,
    submitted_at       timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active          boolean     NOT NULL DEFAULT true,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT training_feedbacks_pkey PRIMARY KEY (feedback_id),
    CONSTRAINT training_feedbacks_session_emp_key UNIQUE (session_id, employee_id),
    CONSTRAINT training_feedbacks_session_id_fkey
        FOREIGN KEY (session_id) REFERENCES training_sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT training_feedbacks_content_rating_check   CHECK (content_rating >= 1 AND content_rating <= 5),
    CONSTRAINT training_feedbacks_relevance_rating_check CHECK (relevance_rating >= 1 AND relevance_rating <= 5),
    CONSTRAINT training_feedbacks_trainer_rating_check   CHECK (trainer_rating >= 1 AND trainer_rating <= 5)
);

CREATE INDEX idx_training_feedbacks_session ON training_feedbacks (session_id);

-- --- certifications ----------------------------------------------------------
-- NOTE: employee_id has no documented FK; replicated as-is.
CREATE TABLE certifications (
    cert_id           uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id       uuid         NOT NULL,
    course_id         uuid         NOT NULL,
    cert_name         varchar(200) NOT NULL,
    cert_name_local   varchar(200),
    issuing_body      varchar(200),
    obtained_date     date         NOT NULL,
    expiry_date       date,
    status            varchar(20)  NOT NULL DEFAULT 'active',
    certificate_url   varchar(500),
    created_by        bigint       NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    is_active         boolean      NOT NULL DEFAULT true,
    CONSTRAINT certifications_pkey PRIMARY KEY (cert_id),
    CONSTRAINT certifications_course_id_fkey
        FOREIGN KEY (course_id) REFERENCES training_courses (course_id) ON DELETE RESTRICT,
    CONSTRAINT certifications_status_check
        CHECK (status IN ('active','expiring_soon','expired','revoked'))
);

CREATE INDEX idx_certifications_emp_expiry ON certifications (employee_id, expiry_date);
CREATE INDEX idx_certifications_status     ON certifications (status);
