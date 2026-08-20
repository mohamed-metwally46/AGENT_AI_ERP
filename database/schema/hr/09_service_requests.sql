-- =============================================================================
-- HR :: Service Requests (letters, tickets, templates)
-- Derived from schema_split/HR/ServiceRequests.md
-- =============================================================================

-- --- sr_letter_templates -----------------------------------------------------
CREATE TABLE sr_letter_templates (
    template_id           uuid         NOT NULL DEFAULT gen_random_uuid(),
    code                  varchar(40)  NOT NULL,
    name                  varchar(120) NOT NULL,
    body_ar               text         NOT NULL,
    body_en               text         NOT NULL,
    header_footer_config  jsonb,
    version               integer      NOT NULL DEFAULT 1,
    created_at            timestamptz,
    updated_at            timestamptz,
    is_active             boolean      NOT NULL DEFAULT true,
    created_by            bigint,
    CONSTRAINT sr_letter_templates_pkey PRIMARY KEY (template_id),
    CONSTRAINT sr_letter_templates_code_key UNIQUE (code)
);

-- --- sr_request_types --------------------------------------------------------
-- NOTE: letter_template_id has no documented FK; replicated as-is.
CREATE TABLE sr_request_types (
    type_id             uuid         NOT NULL DEFAULT gen_random_uuid(),
    code                varchar(40)  NOT NULL,
    name_ar             varchar(120) NOT NULL,
    name_en             varchar(120) NOT NULL,
    category            varchar(20)  NOT NULL,
    requires_approval   boolean      NOT NULL DEFAULT true,
    sla_hours           integer      NOT NULL DEFAULT 48,
    letter_template_id  uuid,
    is_active           boolean      NOT NULL DEFAULT true,
    created_at          timestamptz,
    updated_at          timestamptz,
    created_by          bigint,
    CONSTRAINT sr_request_types_pkey PRIMARY KEY (type_id),
    CONSTRAINT sr_request_types_code_key UNIQUE (code),
    CONSTRAINT sr_request_types_category_check CHECK (category IN ('letter','ticket'))
);

CREATE INDEX idx_sr_request_types_category ON sr_request_types (category);

-- --- sr_requests -------------------------------------------------------------
-- NOTE: employee_id / assigned_to / approval_request_id / output_document_id
-- have no documented FKs; replicated as-is.
CREATE TABLE sr_requests (
    request_id           uuid        NOT NULL DEFAULT gen_random_uuid(),
    request_no           varchar(30) NOT NULL,
    employee_id          uuid        NOT NULL,
    request_type_id      uuid        NOT NULL,
    category             varchar(20) NOT NULL,
    payload              jsonb,
    status               varchar(20) NOT NULL DEFAULT 'submitted',
    approval_request_id  uuid,
    assigned_to          uuid,
    sla_due_at           timestamptz NOT NULL,
    resolved_at          timestamptz,
    output_document_id   uuid,
    created_by           bigint      NOT NULL,
    created_at           timestamptz,
    updated_at           timestamptz,
    is_active            boolean     NOT NULL DEFAULT true,
    CONSTRAINT sr_requests_pkey PRIMARY KEY (request_id),
    CONSTRAINT sr_requests_request_no_key UNIQUE (request_no),
    CONSTRAINT sr_requests_request_type_id_fkey
        FOREIGN KEY (request_type_id) REFERENCES sr_request_types (type_id) ON DELETE RESTRICT,
    CONSTRAINT sr_requests_category_check CHECK (category IN ('letter','ticket')),
    CONSTRAINT sr_requests_status_check
        CHECK (status IN ('submitted','pending_approval','in_progress','resolved','rejected','closed'))
);

CREATE INDEX idx_sr_requests_employee_id ON sr_requests (employee_id);
CREATE INDEX idx_sr_requests_status      ON sr_requests (status);

-- --- sr_generated_letters ----------------------------------------------------
-- NOTE: request_id has no documented FK; replicated as-is.
CREATE TABLE sr_generated_letters (
    letter_id         uuid         NOT NULL DEFAULT gen_random_uuid(),
    request_id        uuid,
    template_id       uuid         NOT NULL,
    context_snapshot  jsonb,
    pdf_path          varchar(255) NOT NULL,
    reference_no      varchar(40)  NOT NULL,
    generated_by      bigint       NOT NULL,
    generated_at      timestamptz  NOT NULL,
    created_at        timestamptz,
    updated_at        timestamptz,
    is_active         boolean      NOT NULL DEFAULT true,
    CONSTRAINT sr_generated_letters_pkey PRIMARY KEY (letter_id),
    CONSTRAINT sr_generated_letters_reference_no_key UNIQUE (reference_no),
    CONSTRAINT sr_generated_letters_template_id_fkey
        FOREIGN KEY (template_id) REFERENCES sr_letter_templates (template_id) ON DELETE RESTRICT
);

CREATE INDEX idx_sr_generated_letters_request ON sr_generated_letters (request_id);
