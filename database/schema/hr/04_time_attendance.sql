-- =============================================================================
-- HR :: Time & Attendance
-- Derived from schema_split/HR/TimeAttendance.md
-- =============================================================================

-- --- work_shifts -------------------------------------------------------------
CREATE TABLE work_shifts (
    shift_id                    uuid          NOT NULL DEFAULT gen_random_uuid(),
    shift_code                  varchar(20)   NOT NULL,
    shift_name                  varchar(100)  NOT NULL,
    shift_name_local            varchar(100),
    start_time                  time          NOT NULL,
    end_time                    time          NOT NULL,
    grace_period_minutes        smallint      NOT NULL DEFAULT 0,
    break_duration_minutes      smallint      NOT NULL DEFAULT 0,
    overtime_threshold_minutes  smallint      NOT NULL DEFAULT 60,
    geofence_latitude           numeric(10,7),
    geofence_longitude          numeric(10,7),
    geofence_radius_meters      integer,
    is_night_shift              boolean       NOT NULL DEFAULT false,
    is_active                   boolean       NOT NULL DEFAULT true,
    created_by                  bigint,
    created_at                  timestamptz,
    updated_at                  timestamptz,
    CONSTRAINT work_shifts_pkey PRIMARY KEY (shift_id),
    CONSTRAINT work_shifts_shift_code_key UNIQUE (shift_code)
);

CREATE INDEX idx_work_shifts_is_active ON work_shifts (is_active);

-- --- shift_assignments -------------------------------------------------------
CREATE TABLE shift_assignments (
    assignment_id   uuid        NOT NULL DEFAULT gen_random_uuid(),
    employee_id     uuid        NOT NULL,
    shift_id        uuid        NOT NULL,
    effective_from  date        NOT NULL,
    effective_to    date,
    assigned_by     bigint,
    created_at      timestamptz,
    updated_at      timestamptz,
    is_active       boolean     NOT NULL DEFAULT true,
    created_by      bigint,
    CONSTRAINT shift_assignments_pkey PRIMARY KEY (assignment_id),
    CONSTRAINT shift_assignments_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE,
    CONSTRAINT shift_assignments_shift_id_fkey
        FOREIGN KEY (shift_id) REFERENCES work_shifts (shift_id) ON DELETE CASCADE
);

CREATE INDEX idx_shift_assignments_emp_period ON shift_assignments (employee_id, effective_from, effective_to);
CREATE INDEX idx_shift_assignments_employee   ON shift_assignments (employee_id);

-- --- attendance_records ------------------------------------------------------
CREATE TABLE attendance_records (
    record_id                uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id              uuid         NOT NULL,
    attendance_date          date         NOT NULL,
    shift_id                 uuid,
    clock_in_at              timestamptz,
    clock_out_at             timestamptz,
    clock_in_source          varchar(20)  NOT NULL DEFAULT 'web',
    clock_out_source         varchar(20),
    clock_in_location        jsonb,
    clock_out_location       jsonb,
    late_minutes             smallint     NOT NULL DEFAULT 0,
    early_departure_minutes  smallint     NOT NULL DEFAULT 0,
    hours_worked             numeric(4,2) NOT NULL DEFAULT 0,
    overtime_minutes         smallint     NOT NULL DEFAULT 0,
    notes                    text,
    created_at               timestamptz,
    updated_at               timestamptz,
    is_active                boolean      NOT NULL DEFAULT true,
    created_by               bigint,
    CONSTRAINT attendance_records_pkey PRIMARY KEY (record_id),
    CONSTRAINT attendance_records_emp_date_key UNIQUE (employee_id, attendance_date),
    CONSTRAINT attendance_records_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE,
    CONSTRAINT attendance_records_shift_id_fkey
        FOREIGN KEY (shift_id) REFERENCES work_shifts (shift_id) ON DELETE SET NULL,
    CONSTRAINT attendance_records_clock_in_source_check
        CHECK (clock_in_source IN ('web','mobile','kiosk')),
    CONSTRAINT attendance_records_clock_out_source_check
        CHECK (clock_out_source IS NULL OR clock_out_source IN ('web','mobile','kiosk'))
);

CREATE INDEX idx_attendance_records_date ON attendance_records (attendance_date);

-- --- attendance_summaries ----------------------------------------------------
CREATE TABLE attendance_summaries (
    summary_id                    uuid         NOT NULL DEFAULT gen_random_uuid(),
    employee_id                   uuid         NOT NULL,
    year                          smallint     NOT NULL,
    month                         smallint     NOT NULL,
    working_days_in_month         smallint     NOT NULL DEFAULT 0,
    days_present                  smallint     NOT NULL DEFAULT 0,
    days_absent                   smallint     NOT NULL DEFAULT 0,
    days_on_leave                 smallint     NOT NULL DEFAULT 0,
    total_hours_worked            numeric(6,2) NOT NULL DEFAULT 0,
    total_late_minutes            integer      NOT NULL DEFAULT 0,
    total_overtime_minutes        integer      NOT NULL DEFAULT 0,
    total_early_departure_minutes integer      NOT NULL DEFAULT 0,
    created_at                    timestamptz,
    updated_at                    timestamptz,
    is_active                     boolean      NOT NULL DEFAULT true,
    created_by                    bigint,
    CONSTRAINT attendance_summaries_pkey PRIMARY KEY (summary_id),
    CONSTRAINT attendance_summaries_emp_year_month_key UNIQUE (employee_id, year, month),
    CONSTRAINT attendance_summaries_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE
);

-- --- overtime_requests -------------------------------------------------------
CREATE TABLE overtime_requests (
    overtime_id           uuid        NOT NULL DEFAULT gen_random_uuid(),
    employee_id           uuid        NOT NULL,
    attendance_record_id  uuid,
    overtime_date         date        NOT NULL,
    requested_minutes     smallint    NOT NULL,
    approved_minutes      smallint,
    reason                text,
    status                varchar(20) NOT NULL DEFAULT 'pending',
    approval_request_id   uuid,
    reviewed_by_emp_id    uuid,
    reviewed_at           timestamptz,
    review_notes          text,
    created_at            timestamptz,
    updated_at            timestamptz,
    is_active             boolean     NOT NULL DEFAULT true,
    created_by            bigint,
    CONSTRAINT overtime_requests_pkey PRIMARY KEY (overtime_id),
    CONSTRAINT overtime_requests_attendance_record_id_fkey
        FOREIGN KEY (attendance_record_id) REFERENCES attendance_records (record_id) ON DELETE SET NULL,
    CONSTRAINT overtime_requests_employee_id_fkey
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE,
    CONSTRAINT overtime_requests_status_check CHECK (status IN ('pending','approved','rejected')),
    CONSTRAINT overtime_requests_requested_minutes_check CHECK (requested_minutes > 0)
);

CREATE INDEX idx_overtime_requests_employee_id ON overtime_requests (employee_id);
CREATE INDEX idx_overtime_requests_status      ON overtime_requests (status);
