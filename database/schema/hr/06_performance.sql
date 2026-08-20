-- =============================================================================
-- HR :: Performance (cycles, appraisals, goals, KPIs, feedback, calibration)
-- Derived from schema_split/HR/Performance.md
-- =============================================================================

-- --- review_cycles -----------------------------------------------------------
CREATE TABLE review_cycles (
    cycle_id          uuid         NOT NULL DEFAULT gen_random_uuid(),
    cycle_name        varchar(200) NOT NULL,
    cycle_name_local  varchar(200),
    cycle_type        varchar(30)  NOT NULL,
    period_start      date         NOT NULL,
    period_end        date         NOT NULL,
    phases_json       jsonb,
    rating_scale      smallint     NOT NULL DEFAULT 5,
    status            varchar(30)  NOT NULL DEFAULT 'draft',
    is_active         boolean      NOT NULL DEFAULT true,
    created_by        bigint       NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    CONSTRAINT review_cycles_pkey PRIMARY KEY (cycle_id),
    CONSTRAINT review_cycles_period_check CHECK (period_end > period_start),
    CONSTRAINT review_cycles_status_check
        CHECK (status IN ('draft','active','self_review','manager_review','calibration','completed','closed')),
    CONSTRAINT review_cycles_type_check
        CHECK (cycle_type IN ('annual','semi_annual','quarterly','probation','project')),
    CONSTRAINT review_cycles_rating_scale_check CHECK (rating_scale >= 3 AND rating_scale <= 10)
);

CREATE INDEX idx_review_cycles_status ON review_cycles (status);
CREATE INDEX idx_review_cycles_type   ON review_cycles (cycle_type);

-- --- cycle_participants ------------------------------------------------------
CREATE TABLE cycle_participants (
    participant_id     uuid        NOT NULL DEFAULT gen_random_uuid(),
    cycle_id           uuid        NOT NULL,
    employee_id        uuid        NOT NULL,
    reviewer_emp_id    uuid,
    phase_status       varchar(30) NOT NULL DEFAULT 'pending',
    self_done_at       timestamptz,
    manager_done_at    timestamptz,
    is_active          boolean     NOT NULL DEFAULT true,
    created_at         timestamptz,
    updated_at         timestamptz,
    created_by         bigint,
    CONSTRAINT cycle_participants_pkey PRIMARY KEY (participant_id),
    CONSTRAINT cycle_participants_cycle_emp_key UNIQUE (cycle_id, employee_id),
    CONSTRAINT cycle_participants_cycle_id_fkey
        FOREIGN KEY (cycle_id) REFERENCES review_cycles (cycle_id) ON DELETE RESTRICT,
    CONSTRAINT cycle_participants_phase_status_check
        CHECK (phase_status IN ('pending','self_in_progress','self_done','manager_in_progress',
                                'manager_done','calibrated','finalised'))
);

CREATE INDEX idx_cycle_participants_cycle   ON cycle_participants (cycle_id);
CREATE INDEX idx_cycle_participants_emp     ON cycle_participants (employee_id);
CREATE INDEX idx_cycle_participants_phase   ON cycle_participants (cycle_id, phase_status);

-- --- appraisals --------------------------------------------------------------
CREATE TABLE appraisals (
    appraisal_id          uuid         NOT NULL DEFAULT gen_random_uuid(),
    cycle_id              uuid         NOT NULL,
    employee_id           uuid         NOT NULL,
    reviewer_emp_id       uuid,
    self_score            numeric(5,2),
    manager_score         numeric(5,2),
    final_score           numeric(5,2),
    status                varchar(30)  NOT NULL DEFAULT 'pending',
    self_submitted_at     timestamptz,
    manager_submitted_at  timestamptz,
    acknowledged_at       timestamptz,
    is_active             boolean      NOT NULL DEFAULT true,
    created_by            bigint       NOT NULL,
    created_at            timestamptz,
    updated_at            timestamptz,
    CONSTRAINT appraisals_pkey PRIMARY KEY (appraisal_id),
    CONSTRAINT appraisals_cycle_emp_key UNIQUE (cycle_id, employee_id),
    CONSTRAINT appraisals_cycle_id_fkey
        FOREIGN KEY (cycle_id) REFERENCES review_cycles (cycle_id) ON DELETE RESTRICT,
    CONSTRAINT appraisals_status_check
        CHECK (status IN ('pending','self_in_progress','self_submitted','manager_in_progress',
                          'manager_submitted','finalised','acknowledged','calibrated'))
);

CREATE INDEX idx_appraisals_cycle        ON appraisals (cycle_id);
CREATE INDEX idx_appraisals_employee     ON appraisals (employee_id);
CREATE INDEX idx_appraisals_cycle_status ON appraisals (cycle_id, status);

-- --- appraisal_ratings -------------------------------------------------------
CREATE TABLE appraisal_ratings (
    rating_id        uuid         NOT NULL DEFAULT gen_random_uuid(),
    appraisal_id     uuid         NOT NULL,
    item_type        varchar(20)  NOT NULL,
    item_id          uuid         NOT NULL,
    weight_pct       numeric(5,2) NOT NULL DEFAULT 0,
    self_rating      numeric(5,2),
    manager_rating   numeric(5,2),
    self_comment     text,
    manager_comment  text,
    created_at       timestamptz,
    updated_at       timestamptz,
    is_active        boolean      NOT NULL DEFAULT true,
    CONSTRAINT appraisal_ratings_pkey PRIMARY KEY (rating_id),
    CONSTRAINT appraisal_ratings_appraisal_id_fkey
        FOREIGN KEY (appraisal_id) REFERENCES appraisals (appraisal_id) ON DELETE CASCADE,
    CONSTRAINT appraisal_ratings_item_type_check CHECK (item_type IN ('kpi','goal','competency')),
    CONSTRAINT appraisal_ratings_weight_check CHECK (weight_pct >= 0 AND weight_pct <= 100)
);

CREATE INDEX idx_appraisal_ratings_appraisal ON appraisal_ratings (appraisal_id);
CREATE INDEX idx_appraisal_ratings_item      ON appraisal_ratings (item_type, item_id);

-- --- calibration_sessions ----------------------------------------------------
CREATE TABLE calibration_sessions (
    session_id           uuid        NOT NULL DEFAULT gen_random_uuid(),
    cycle_id             uuid        NOT NULL,
    scope                varchar(20) NOT NULL DEFAULT 'dept',
    scope_id             uuid,
    status               varchar(20) NOT NULL DEFAULT 'draft',
    facilitator_emp_id   uuid,
    finalised_at         timestamptz,
    is_active            boolean     NOT NULL DEFAULT true,
    created_by           bigint      NOT NULL,
    created_at           timestamptz,
    updated_at           timestamptz,
    CONSTRAINT calibration_sessions_pkey PRIMARY KEY (session_id),
    CONSTRAINT calibration_sessions_cycle_id_fkey
        FOREIGN KEY (cycle_id) REFERENCES review_cycles (cycle_id) ON DELETE RESTRICT,
    CONSTRAINT calibration_sessions_scope_check CHECK (scope IN ('all','dept','grade')),
    CONSTRAINT calibration_sessions_status_check CHECK (status IN ('draft','in_progress','finalised'))
);

CREATE INDEX idx_calibration_sessions_cycle  ON calibration_sessions (cycle_id);
CREATE INDEX idx_calibration_sessions_status ON calibration_sessions (status);

-- --- calibration_entries -----------------------------------------------------
CREATE TABLE calibration_entries (
    entry_id           uuid         NOT NULL DEFAULT gen_random_uuid(),
    session_id         uuid         NOT NULL,
    employee_id        uuid         NOT NULL,
    original_rating    numeric(5,2),
    calibrated_rating  numeric(5,2),
    performance_axis   numeric(5,2),
    potential_axis     numeric(5,2),
    box_position       smallint,
    notes              text,
    created_at         timestamptz,
    updated_at         timestamptz,
    is_active          boolean      NOT NULL DEFAULT true,
    created_by         bigint,
    CONSTRAINT calibration_entries_pkey PRIMARY KEY (entry_id),
    CONSTRAINT calibration_entries_session_id_fkey
        FOREIGN KEY (session_id) REFERENCES calibration_sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT calibration_entries_box_position_check
        CHECK (box_position IS NULL OR (box_position >= 1 AND box_position <= 9))
);

CREATE INDEX idx_calibration_entries_employee ON calibration_entries (employee_id);
CREATE INDEX idx_calibration_entries_session  ON calibration_entries (session_id);

-- --- competencies ------------------------------------------------------------
CREATE TABLE competencies (
    competency_id          uuid         NOT NULL DEFAULT gen_random_uuid(),
    competency_name        varchar(200) NOT NULL,
    competency_name_local  varchar(200),
    grade_scope            uuid,
    description            text,
    is_active              boolean      NOT NULL DEFAULT true,
    created_by             bigint       NOT NULL,
    created_at             timestamptz,
    updated_at             timestamptz,
    CONSTRAINT competencies_pkey PRIMARY KEY (competency_id)
);

CREATE INDEX idx_competencies_grade_scope ON competencies (grade_scope);
CREATE INDEX idx_competencies_is_active   ON competencies (is_active);

-- --- feedback_requests -------------------------------------------------------
CREATE TABLE feedback_requests (
    request_id      uuid        NOT NULL DEFAULT gen_random_uuid(),
    cycle_id        uuid        NOT NULL,
    subject_emp_id  uuid        NOT NULL,
    rater_emp_id    uuid        NOT NULL,
    relationship    varchar(20) NOT NULL,
    status          varchar(20) NOT NULL DEFAULT 'sent',
    sent_at         timestamptz,
    responded_at    timestamptz,
    is_active       boolean     NOT NULL DEFAULT true,
    created_at      timestamptz,
    updated_at      timestamptz,
    created_by      bigint,
    CONSTRAINT feedback_requests_pkey PRIMARY KEY (request_id),
    CONSTRAINT feedback_requests_cycle_id_fkey
        FOREIGN KEY (cycle_id) REFERENCES review_cycles (cycle_id) ON DELETE RESTRICT,
    CONSTRAINT feedback_requests_relationship_check
        CHECK (relationship IN ('peer','subordinate','manager','self','external')),
    CONSTRAINT feedback_requests_status_check
        CHECK (status IN ('pending','sent','responded','declined','expired'))
);

CREATE INDEX idx_feedback_requests_cycle   ON feedback_requests (cycle_id);
CREATE INDEX idx_feedback_requests_rater   ON feedback_requests (rater_emp_id);
CREATE INDEX idx_feedback_requests_subject ON feedback_requests (subject_emp_id);

-- --- feedback_responses ------------------------------------------------------
CREATE TABLE feedback_responses (
    response_id   uuid        NOT NULL DEFAULT gen_random_uuid(),
    request_id    uuid        NOT NULL,
    ratings_json  jsonb,
    strengths     text,
    improvements  text,
    is_anonymous  boolean     NOT NULL DEFAULT false,
    submitted_at  timestamptz,
    created_at    timestamptz,
    updated_at    timestamptz,
    is_active     boolean     NOT NULL DEFAULT true,
    created_by    bigint,
    CONSTRAINT feedback_responses_pkey PRIMARY KEY (response_id),
    CONSTRAINT feedback_responses_request_id_key UNIQUE (request_id),
    CONSTRAINT feedback_responses_request_id_fkey
        FOREIGN KEY (request_id) REFERENCES feedback_requests (request_id) ON DELETE CASCADE
);

-- --- goals -------------------------------------------------------------------
CREATE TABLE goals (
    goal_id              uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id          uuid         NOT NULL,
    title                varchar(255) NOT NULL,
    title_local          varchar(255),
    description          text,
    goal_type            varchar(20)  NOT NULL,
    category             varchar(50),
    weight_pct           numeric(5,2) NOT NULL DEFAULT 0,
    parent_goal_id       uuid,
    cycle_id             uuid,
    start_date           date         NOT NULL,
    due_date             date         NOT NULL,
    progress_pct         numeric(5,2) NOT NULL DEFAULT 0,
    status               varchar(20)  NOT NULL DEFAULT 'draft',
    approval_request_id  uuid,
    is_active            boolean      NOT NULL DEFAULT true,
    created_by           bigint       NOT NULL,
    created_at           timestamptz,
    updated_at           timestamptz,
    CONSTRAINT goals_pkey PRIMARY KEY (goal_id),
    CONSTRAINT goals_parent_goal_id_fkey
        FOREIGN KEY (parent_goal_id) REFERENCES goals (goal_id) ON DELETE RESTRICT,
    CONSTRAINT goals_progress_check CHECK (progress_pct >= 0 AND progress_pct <= 100),
    CONSTRAINT goals_status_check
        CHECK (status IN ('draft','submitted','approved','active','completed','cancelled')),
    CONSTRAINT goals_type_check CHECK (goal_type IN ('smart','okr')),
    CONSTRAINT goals_weight_check CHECK (weight_pct >= 0 AND weight_pct <= 100)
);

CREATE INDEX idx_goals_cycle      ON goals (cycle_id);
CREATE INDEX idx_goals_emp_cycle  ON goals (employee_id, cycle_id);
CREATE INDEX idx_goals_employee   ON goals (employee_id);
CREATE INDEX idx_goals_parent     ON goals (parent_goal_id);
CREATE INDEX idx_goals_status     ON goals (status);

-- --- goal_checkins -----------------------------------------------------------
CREATE TABLE goal_checkins (
    checkin_id    uuid         NOT NULL DEFAULT gen_random_uuid(),
    goal_id       uuid         NOT NULL,
    progress_pct  numeric(5,2) NOT NULL,
    comment       text,
    created_by    bigint       NOT NULL,
    created_at    timestamptz,
    updated_at    timestamptz,
    is_active     boolean      NOT NULL DEFAULT true,
    CONSTRAINT goal_checkins_pkey PRIMARY KEY (checkin_id),
    CONSTRAINT goal_checkins_goal_id_fkey
        FOREIGN KEY (goal_id) REFERENCES goals (goal_id) ON DELETE CASCADE,
    CONSTRAINT goal_checkins_progress_check CHECK (progress_pct >= 0 AND progress_pct <= 100)
);

CREATE INDEX idx_goal_checkins_goal ON goal_checkins (goal_id);

-- --- key_results -------------------------------------------------------------
-- NOTE: currency_code documented as bare `char` with default 'EGP'; inferred char(3).
CREATE TABLE key_results (
    kr_id          uuid          NOT NULL DEFAULT gen_random_uuid(),
    goal_id        uuid          NOT NULL,
    description    varchar(500)  NOT NULL,
    metric_type    varchar(20)   NOT NULL,
    currency_code  char(3)       NOT NULL DEFAULT 'EGP',
    start_value    numeric(15,2) NOT NULL DEFAULT 0,
    target_value   numeric(15,2) NOT NULL,
    current_value  numeric(15,2) NOT NULL DEFAULT 0,
    progress_pct   numeric(5,2)  NOT NULL DEFAULT 0,
    is_active      boolean       NOT NULL DEFAULT true,
    created_at     timestamptz,
    updated_at     timestamptz,
    created_by     bigint,
    CONSTRAINT key_results_pkey PRIMARY KEY (kr_id),
    CONSTRAINT key_results_goal_id_fkey
        FOREIGN KEY (goal_id) REFERENCES goals (goal_id) ON DELETE CASCADE,
    CONSTRAINT key_results_metric_type_check
        CHECK (metric_type IN ('number','percentage','currency','boolean')),
    CONSTRAINT key_results_progress_check CHECK (progress_pct >= 0 AND progress_pct <= 100)
);

CREATE INDEX idx_key_results_goal ON key_results (goal_id);

-- --- kpi_library -------------------------------------------------------------
CREATE TABLE kpi_library (
    kpi_id            uuid         NOT NULL DEFAULT gen_random_uuid(),
    kpi_code          varchar(30)  NOT NULL,
    kpi_name          varchar(200) NOT NULL,
    kpi_name_local    varchar(200) NOT NULL,
    category          varchar(50)  NOT NULL,
    unit              varchar(50),
    direction         varchar(20)  NOT NULL,
    measurement_type  varchar(20)  NOT NULL,
    formula           text,
    data_source       varchar(100),
    description       text,
    is_active         boolean      NOT NULL DEFAULT true,
    created_by        bigint       NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    CONSTRAINT kpi_library_pkey PRIMARY KEY (kpi_id),
    CONSTRAINT kpi_library_kpi_code_key UNIQUE (kpi_code),
    CONSTRAINT kpi_library_direction_check
        CHECK (direction IN ('higher_better','lower_better','exact')),
    CONSTRAINT kpi_library_measurement_type_check
        CHECK (measurement_type IN ('number','percentage','currency','ratio','boolean'))
);

CREATE INDEX idx_kpi_library_category  ON kpi_library (category);
CREATE INDEX idx_kpi_library_is_active ON kpi_library (is_active);

-- --- kpi_assignments ---------------------------------------------------------
-- NOTE: currency_code documented as bare `char` with default 'EGP'; inferred char(3).
CREATE TABLE kpi_assignments (
    assignment_id   uuid          NOT NULL DEFAULT gen_random_uuid(),
    kpi_id          uuid          NOT NULL,
    assignee_type   varchar(20)   NOT NULL,
    assignee_id     uuid          NOT NULL,
    cycle_id        uuid,
    weight_pct      numeric(5,2)  NOT NULL DEFAULT 0,
    target_value    numeric(15,2),
    currency_code   char(3)       NOT NULL DEFAULT 'EGP',
    threshold_min   numeric(15,2),
    threshold_max   numeric(15,2),
    period          varchar(7),
    status          varchar(20)   NOT NULL DEFAULT 'active',
    is_active       boolean       NOT NULL DEFAULT true,
    created_by      bigint        NOT NULL,
    created_at      timestamptz,
    updated_at      timestamptz,
    CONSTRAINT kpi_assignments_pkey PRIMARY KEY (assignment_id),
    CONSTRAINT kpi_assignments_kpi_id_fkey
        FOREIGN KEY (kpi_id) REFERENCES kpi_library (kpi_id) ON DELETE RESTRICT,
    CONSTRAINT kpi_assignments_assignee_type_check
        CHECK (assignee_type IN ('employee','role','dept')),
    CONSTRAINT kpi_assignments_status_check CHECK (status IN ('active','inactive','archived')),
    CONSTRAINT kpi_assignments_weight_check CHECK (weight_pct >= 0 AND weight_pct <= 100)
);

CREATE INDEX idx_kpi_assignments_assignee ON kpi_assignments (assignee_type, assignee_id);
CREATE INDEX idx_kpi_assignments_cycle    ON kpi_assignments (cycle_id);
CREATE INDEX idx_kpi_assignments_kpi      ON kpi_assignments (kpi_id);

-- --- improvement_plans -------------------------------------------------------
CREATE TABLE improvement_plans (
    pip_id           uuid        NOT NULL DEFAULT gen_random_uuid(),
    employee_id      uuid        NOT NULL,
    cycle_id         uuid,
    reason           text        NOT NULL,
    objectives_json  jsonb,
    start_date       date        NOT NULL,
    end_date         date        NOT NULL,
    status           varchar(20) NOT NULL DEFAULT 'draft',
    manager_emp_id   uuid,
    hr_emp_id        uuid,
    outcome          text,
    is_active        boolean     NOT NULL DEFAULT true,
    created_by       bigint      NOT NULL,
    created_at       timestamptz,
    updated_at       timestamptz,
    CONSTRAINT improvement_plans_pkey PRIMARY KEY (pip_id),
    CONSTRAINT improvement_plans_dates_check CHECK (end_date > start_date),
    CONSTRAINT improvement_plans_status_check
        CHECK (status IN ('draft','active','monitoring','completed','failed','cancelled'))
);

CREATE INDEX idx_improvement_plans_cycle    ON improvement_plans (cycle_id);
CREATE INDEX idx_improvement_plans_employee ON improvement_plans (employee_id);
CREATE INDEX idx_improvement_plans_status   ON improvement_plans (status);
