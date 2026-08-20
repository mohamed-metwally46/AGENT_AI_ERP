-- =============================================================================
-- mock_hr :: realistic fake HR data
--
-- Targets every table defined in database/schema/hr/ (85 tables).
-- All data respects the documented PKs, FKs, unique keys, check constraints and
-- relationships. Nothing here alters the schema.
--
-- Dates are anchored to CURRENT_DATE so the dataset stays "recent" whenever the
-- volume is first initialised.
-- =============================================================================

BEGIN;

SELECT setseed(0.42);

-- Deterministic pseudo-random helper, dropped with the session (pg_temp).
CREATE FUNCTION pg_temp.h(txt text, m int) RETURNS int
    LANGUAGE sql IMMUTABLE AS
$$ SELECT (((('x' || substr(md5(txt), 1, 8))::bit(32)::int) & 2147483647) % m) $$;

-- =============================================================================
-- 1. Organisation structure
-- =============================================================================

INSERT INTO organization_branches
    (branch_code, branch_name, branch_name_local, branch_type, parent_branch_id,
     country_code, city, address_line1, postal_code, phone, email, tax_id,
     is_head_office, established_date, created_by, created_at, updated_at)
VALUES
    ('BR-HQ',  'Cairo Head Office',      'المقر الرئيسي - القاهرة', 'head_office',     NULL, 'EG', 'Cairo',
     '12 Nile Corniche, Maadi', '11728', '+20 2 2525 1000', 'hq@example-erp.com', 'TX-100200300',
     true,  CURRENT_DATE - 4200, 1, now(), now()),
    ('BR-ALX', 'Alexandria Branch',      'فرع الإسكندرية',        'branch',          NULL, 'EG', 'Alexandria',
     '45 Sidi Gaber Street', '21311', '+20 3 4242 2000', 'alexandria@example-erp.com', 'TX-100200301',
     false, CURRENT_DATE - 3100, 1, now(), now()),
    ('BR-GIZ', 'Giza Regional Office',   'مكتب الجيزة الإقليمي',   'regional_office', NULL, 'EG', 'Giza',
     '8 Gamaet El Dewal El Arabeya', '12511', '+20 2 3333 4000', 'giza@example-erp.com', 'TX-100200302',
     false, CURRENT_DATE - 2400, 1, now(), now()),
    ('BR-6OW', '6th of October Warehouse','مخزن السادس من أكتوبر', 'warehouse',       NULL, 'EG', '6th of October City',
     'Industrial Zone 3, Plot 44', '12573', '+20 2 3838 5000', 'warehouse@example-erp.com', NULL,
     false, CURRENT_DATE - 1800, 1, now(), now());

-- Branch hierarchy: everything reports to the head office.
UPDATE organization_branches ob
SET parent_branch_id = hq.branch_id
FROM organization_branches hq
WHERE hq.branch_code = 'BR-HQ'
  AND ob.branch_code <> 'BR-HQ';

-- --- departments -------------------------------------------------------------
INSERT INTO branch_departments
    (branch_id, dept_code, dept_name, dept_name_local, dept_type,
     headcount_budget, effective_date, created_by, created_at, updated_at)
SELECT b.branch_id, d.dept_code, d.dept_name, d.dept_name_local, d.dept_type,
       d.headcount_budget, CURRENT_DATE - 900, 1, now(), now()
FROM (VALUES
    ('EXE', 'Executive Office',       'المكتب التنفيذي',   'management',     2,  'BR-HQ'),
    ('HR',  'Human Resources',        'الموارد البشرية',    'administrative', 8,  'BR-HQ'),
    ('FIN', 'Finance',                'المالية',            'administrative', 8,  'BR-HQ'),
    ('SLS', 'Sales',                  'المبيعات',           'operational',    16, 'BR-HQ'),
    ('MKT', 'Marketing',              'التسويق',            'operational',    6,  'BR-HQ'),
    ('ENG', 'Engineering',            'الهندسة',            'technical',      14, 'BR-HQ'),
    ('IT',  'Information Technology', 'تكنولوجيا المعلومات','technical',      6,  'BR-HQ'),
    ('OPS', 'Operations',             'العمليات',           'operational',    12, 'BR-6OW'),
    ('LEG', 'Legal',                  'الشؤون القانونية',   'support',        3,  'BR-HQ'),
    ('PRC', 'Procurement',            'المشتريات',          'support',        4,  'BR-6OW'),
    ('CS',  'Customer Support',       'خدمة العملاء',       'support',        9,  'BR-ALX'),
    ('QA',  'Quality Assurance',      'ضمان الجودة',        'technical',      5,  'BR-GIZ')
) AS d(dept_code, dept_name, dept_name_local, dept_type, headcount_budget, branch_code)
JOIN organization_branches b ON b.branch_code = d.branch_code;

-- Departments roll up to the Executive Office.
UPDATE branch_departments bd
SET parent_dept_id = exe.dept_id
FROM branch_departments exe
WHERE exe.dept_code = 'EXE'
  AND bd.dept_code <> 'EXE';

-- --- cost centres ------------------------------------------------------------
INSERT INTO cost_centers
    (branch_id, dept_id, cc_code, cc_name, cc_name_local, cc_type, account_code,
     currency_code, budget_amount, fiscal_year, created_by, created_at, updated_at)
SELECT bd.branch_id, bd.dept_id,
       'CC-' || bd.dept_code,
       bd.dept_name || ' Cost Centre',
       bd.dept_name_local,
       CASE WHEN bd.dept_code IN ('SLS','MKT') THEN 'revenue_centre'
            WHEN bd.dept_code = 'EXE'          THEN 'investment_centre'
            ELSE 'cost_centre' END,
       '5' || lpad((row_number() OVER (ORDER BY bd.dept_code))::text, 5, '0'),
       'EGP',
       (bd.headcount_budget * 420000)::numeric(15,2),
       EXTRACT(YEAR FROM CURRENT_DATE)::smallint,
       1, now(), now()
FROM branch_departments bd;

UPDATE branch_departments bd
SET cost_centre_id = cc.cost_centre_id
FROM cost_centers cc
WHERE cc.dept_id = bd.dept_id;

-- =============================================================================
-- 2. Job architecture
-- =============================================================================

INSERT INTO job_grades
    (grade_code, grade_name, grade_name_local, grade_level, grade_category,
     min_salary, mid_salary, max_salary, currency_code, description,
     overtime_eligible, annual_leave_days, sick_leave_days, created_by, created_at, updated_at)
VALUES
    ('G1', 'Executive',            'تنفيذي',            8, 'executive',      120000, 180000, 250000, 'EGP', 'C-level leadership',            false, 30, 15, 1, now(), now()),
    ('G2', 'Director',             'مدير عام',          7, 'management',      70000,  92000, 120000, 'EGP', 'Function heads',                false, 28, 15, 1, now(), now()),
    ('G3', 'Senior Manager',       'مدير أول',          6, 'management',      50000,  62000,  80000, 'EGP', 'Senior people managers',        false, 25, 15, 1, now(), now()),
    ('G4', 'Manager',              'مدير',              5, 'management',      35000,  44000,  58000, 'EGP', 'First-line managers',           false, 24, 12, 1, now(), now()),
    ('G5', 'Senior Professional',  'أخصائي أول',        4, 'professional',    25000,  31000,  40000, 'EGP', 'Senior individual contributors',false, 21, 12, 1, now(), now()),
    ('G6', 'Professional',         'أخصائي',            3, 'professional',    16000,  20000,  26000, 'EGP', 'Individual contributors',       true,  21, 12, 1, now(), now()),
    ('G7', 'Junior Professional',  'أخصائي مبتدئ',      2, 'technical',       10000,  13000,  17000, 'EGP', 'Early career',                  true,  18, 10, 1, now(), now()),
    ('G8', 'Entry Level',          'مستوى مبتدئ',       1, 'operational',      6000,   8000,  11000, 'EGP', 'Entry / operational roles',     true,  15, 10, 1, now(), now());

-- Two jobs per department: a managerial job and a specialist job.
INSERT INTO job_catalog
    (job_grade_id, job_code, job_title, job_title_local, job_family, job_subfamily,
     job_level, job_category, job_description, qualifications_required, skills_required,
     min_experience_years, requires_approval, created_by, created_at, updated_at)
SELECT g.job_grade_id,
       'MGR-' || bd.dept_code,
       bd.dept_name || ' Manager',
       'مدير ' || bd.dept_name_local,
       bd.dept_name, 'Management', 'manager', 'full_time',
       'Leads the ' || bd.dept_name || ' function, owns budget and team delivery.',
       'Bachelor degree; 8+ years of relevant experience.',
       'Leadership, planning, stakeholder management',
       8, true, 1, now(), now()
FROM branch_departments bd
JOIN job_grades g ON g.grade_code = 'G4'
UNION ALL
SELECT g.job_grade_id,
       'SPC-' || bd.dept_code,
       bd.dept_name || ' Specialist',
       'أخصائي ' || bd.dept_name_local,
       bd.dept_name, 'Professional', 'mid', 'full_time',
       'Delivers day-to-day ' || bd.dept_name || ' work.',
       'Bachelor degree; 3+ years of relevant experience.',
       'Domain expertise, communication, problem solving',
       3, false, 1, now(), now()
FROM branch_departments bd
JOIN job_grades g ON g.grade_code = 'G6';

-- One managerial and one specialist position per department.
INSERT INTO positions
    (position_code, position_title, position_title_local, dept_id, branch_id, job_id,
     cost_centre_id, headcount_budget, position_status, effective_date, created_by, created_at, updated_at)
SELECT 'POS-' || bd.dept_code || '-MGR', jc.job_title, jc.job_title_local,
       bd.dept_id, bd.branch_id, jc.job_id, bd.cost_centre_id,
       2, 'filled', CURRENT_DATE - 900, 1, now(), now()
FROM branch_departments bd
JOIN job_catalog jc ON jc.job_code = 'MGR-' || bd.dept_code
UNION ALL
SELECT 'POS-' || bd.dept_code || '-SPC', jc.job_title, jc.job_title_local,
       bd.dept_id, bd.branch_id, jc.job_id, bd.cost_centre_id,
       GREATEST(bd.headcount_budget - 2, 1)::smallint, 'open', CURRENT_DATE - 900, 1, now(), now()
FROM branch_departments bd
JOIN job_catalog jc ON jc.job_code = 'SPC-' || bd.dept_code;

-- =============================================================================
-- 3. Employees (60) with a real reporting hierarchy and a mix of statuses
-- =============================================================================

INSERT INTO employees
    (employee_number, branch_id, dept_id, position_id, job_grade_id, cost_centre_id,
     first_name, middle_name, last_name, first_name_local, last_name_local,
     national_id, national_id_type, passport_number, passport_expiry,
     date_of_birth, place_of_birth, gender, marital_status, nationality, religion, blood_type,
     email_work, email_personal, phone_work, phone_mobile,
     address_line1, city, state, country, postal_code,
     employment_status, employment_type, hire_date, seniority_date, confirmation_date,
     termination_date, termination_reason, termination_type,
     is_active, created_by, created_at, updated_at, locale, theme, eligible_for_rehire)
SELECT
    'EMP-' || lpad(n::text, 4, '0'),
    bd.branch_id,
    bd.dept_id,
    p.position_id,
    g.job_grade_id,
    bd.cost_centre_id,
    nm.first_name,
    (ARRAY['Ahmed','Mohamed','Sayed','Fathy','Adel','Hosny'])[1 + (n % 6)],
    nm.last_name,
    nm.first_name_local,
    nm.last_name_local,
    '2' || to_char(dob.d, 'YYMMDD') || lpad(n::text, 7, '0'),
    'national_id',
    'A' || lpad((11000000 + n * 137)::text, 8, '0'),
    CURRENT_DATE + (400 + n * 11),
    dob.d,
    (ARRAY['Cairo','Alexandria','Giza','Mansoura','Tanta','Aswan'])[1 + (n % 6)],
    nm.gender,
    (ARRAY['single','married','married','divorced','married','single'])[1 + (n % 6)],
    'EGY',
    (ARRAY['Islam','Christianity'])[1 + (n % 2)],
    (ARRAY['a+','a-','b+','b-','ab+','ab-','o+','o-'])[1 + (n % 8)],
    lower(nm.first_name) || '.' || lower(replace(nm.last_name, ' ', '')) || '.' || n || '@example-erp.com',
    lower(nm.first_name) || n || '@personal-mail.com',
    '+20 2 2525 1' || lpad(n::text, 3, '0'),
    '+20 10' || lpad((10000000 + n * 91711)::text, 8, '0'),
    (10 + n) || ' ' || (ARRAY['El Nasr Street','Tahrir Square','Ramses Road','El Horreya Avenue','Gamal Abdel Nasser St'])[1 + (n % 5)],
    (ARRAY['Cairo','Alexandria','Giza','6th of October City'])[1 + (n % 4)],
    (ARRAY['Cairo','Alexandria','Giza','Giza'])[1 + (n % 4)],
    'EGY',
    lpad((11000 + n * 7)::text, 5, '0'),
    st.employment_status,
    CASE WHEN n IN (53, 54) THEN 'contract'
         WHEN n IN (48, 49) THEN 'internship'
         WHEN n = 51        THEN 'part_time'
         ELSE 'full_time' END,
    hd.d,
    hd.d,
    CASE WHEN st.employment_status = 'probation' THEN NULL ELSE hd.d + 90 END,
    CASE WHEN st.employment_status IN ('terminated','resigned','retired')
         THEN GREATEST(hd.d + 400, CURRENT_DATE - (30 + n)) END,
    CASE WHEN st.employment_status = 'terminated' THEN 'Repeated performance concerns after PIP.'
         WHEN st.employment_status = 'resigned'   THEN 'Accepted an external offer.'
         WHEN st.employment_status = 'retired'    THEN 'Reached statutory retirement age.' END,
    CASE WHEN st.employment_status = 'terminated' THEN 'termination'
         WHEN st.employment_status = 'resigned'   THEN 'resignation'
         WHEN st.employment_status = 'retired'    THEN 'retirement' END,
    st.employment_status NOT IN ('terminated','resigned','retired','inactive'),
    1, now(), now(),
    (ARRAY['en','ar'])[1 + (n % 2)],
    (ARRAY['light','dark'])[1 + (n % 2)],
    CASE WHEN st.employment_status = 'terminated' THEN false
         WHEN st.employment_status IN ('resigned','retired') THEN true END
FROM generate_series(1, 60) AS n
CROSS JOIN LATERAL (
    SELECT (ARRAY[
        -- 1..5 exec/directors, 6..15 managers, 16..60 staff
        'EXE','HR','FIN','SLS','ENG',
        'HR','FIN','SLS','SLS','ENG','IT','OPS','CS','QA','MKT',
        'SLS','SLS','SLS','SLS','SLS','SLS','SLS','SLS','SLS',
        'ENG','ENG','ENG','ENG','ENG','ENG','ENG','ENG',
        'OPS','OPS','OPS','OPS','OPS','OPS','OPS',
        'CS','CS','CS','CS','CS',
        'FIN','FIN','FIN',
        'HR','HR','HR',
        'MKT','MKT','MKT',
        'IT','IT','IT',
        'QA','QA',
        'PRC',
        'LEG'
    ])[n] AS dept_code
) AS dc
JOIN branch_departments bd ON bd.dept_code = dc.dept_code
CROSS JOIN LATERAL (
    SELECT CASE WHEN n = 1 THEN 'G1'
                WHEN n BETWEEN 2 AND 5 THEN 'G2'
                WHEN n IN (6, 7) THEN 'G3'
                WHEN n BETWEEN 8 AND 15 THEN 'G4'
                ELSE (ARRAY['G5','G6','G6','G7','G6','G8'])[1 + (n % 6)]
           END AS grade_code
) AS gc
JOIN job_grades g ON g.grade_code = gc.grade_code
JOIN positions p ON p.position_code =
     'POS-' || dc.dept_code || CASE WHEN n <= 15 THEN '-MGR' ELSE '-SPC' END
CROSS JOIN LATERAL (SELECT (CURRENT_DATE - (3200 - n * 48))::date AS d) AS hd
CROSS JOIN LATERAL (SELECT (CURRENT_DATE - (12000 + (n * 211) % 5000))::date AS d) AS dob
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN n IN (52, 57)             THEN 'terminated'
        WHEN n IN (44, 59)             THEN 'resigned'
        WHEN n = 60                    THEN 'retired'
        WHEN n = 38                    THEN 'suspended'
        WHEN n IN (23, 31, 46)         THEN 'on_leave'
        WHEN n IN (48, 49, 50, 55, 58) THEN 'probation'
        WHEN n = 56                    THEN 'inactive'
        ELSE 'active' END AS employment_status
) AS st
CROSS JOIN LATERAL (
    SELECT
        CASE WHEN n % 3 = 0 THEN 'female' ELSE 'male' END AS gender,
        CASE WHEN n % 3 = 0
             THEN (ARRAY['Fatma','Aya','Nour','Salma','Mona','Heba','Yasmin','Dina','Rania','Sara',
                         'Mariam','Nada','Amira','Ghada','Hala','Reem','Menna','Doaa','Shaimaa','Nesreen'])[1 + (n % 20)]
             ELSE (ARRAY['Ahmed','Mohamed','Mahmoud','Omar','Youssef','Khaled','Tarek','Hassan','Amr','Karim',
                         'Mostafa','Ibrahim','Sherif','Waleed','Sameh','Adel','Hany','Ayman','Bassem','Fady'])[1 + (n % 20)]
        END AS first_name,
        CASE WHEN n % 3 = 0
             THEN (ARRAY['فاطمة','آية','نور','سلمى','منى','هبة','ياسمين','دينا','رانيا','سارة',
                         'مريم','ندى','أميرة','غادة','هالة','ريم','منة','دعاء','شيماء','نسرين'])[1 + (n % 20)]
             ELSE (ARRAY['أحمد','محمد','محمود','عمر','يوسف','خالد','طارق','حسن','عمرو','كريم',
                         'مصطفى','إبراهيم','شريف','وليد','سامح','عادل','هاني','أيمن','باسم','فادي'])[1 + (n % 20)]
        END AS first_name_local,
        (ARRAY['Hassan','Ali','Mahmoud','Ibrahim','Said','Fahmy','Nassar','Zaki','Shaker','Farouk',
               'Kamel','Sabry','Refaat','Gaber','Lotfy','Mansour','Selim','Ragab','Hamdy','Abdel Aziz'])[1 + ((n * 3) % 20)] AS last_name,
        (ARRAY['حسن','علي','محمود','إبراهيم','سعيد','فهمي','نصار','زكي','شاكر','فاروق',
               'كامل','صبري','رفعت','جابر','لطفي','منصور','سليم','رجب','حمدي','عبد العزيز'])[1 + ((n * 3) % 20)] AS last_name_local
) AS nm;

-- --- reporting lines ---------------------------------------------------------
-- Directors (0002-0005) report to the CEO (0001).
UPDATE employees e
SET manager_id = ceo.employee_id
FROM employees ceo
WHERE ceo.employee_number = 'EMP-0001'
  AND e.employee_number IN ('EMP-0002','EMP-0003','EMP-0004','EMP-0005');

-- Managers (0006-0015) report to a director.
UPDATE employees e
SET manager_id = mgr.employee_id
FROM employees mgr
WHERE mgr.employee_number =
      'EMP-' || lpad((2 + ((substr(e.employee_number, 5)::int - 6) % 4))::text, 4, '0')
  AND substr(e.employee_number, 5)::int BETWEEN 6 AND 15;

-- Staff (0016-0060) report to a manager.
UPDATE employees e
SET manager_id = mgr.employee_id
FROM employees mgr
WHERE mgr.employee_number =
      'EMP-' || lpad((6 + ((substr(e.employee_number, 5)::int - 16) % 10))::text, 4, '0')
  AND substr(e.employee_number, 5)::int BETWEEN 16 AND 60;

-- Department and branch managers.
UPDATE branch_departments bd
SET manager_emp_id = m.employee_id
FROM (
    SELECT DISTINCT ON (dept_id) dept_id, employee_id
    FROM employees
    WHERE employee_number < 'EMP-0016' AND is_active
    ORDER BY dept_id, employee_number
) m
WHERE m.dept_id = bd.dept_id;

UPDATE organization_branches ob
SET manager_emp_id = m.employee_id
FROM (
    SELECT DISTINCT ON (branch_id) branch_id, employee_id
    FROM employees
    WHERE employee_number < 'EMP-0016' AND is_active
    ORDER BY branch_id, employee_number
) m
WHERE m.branch_id = ob.branch_id;

-- Realised headcounts.
UPDATE branch_departments bd
SET headcount_filled = c.cnt
FROM (SELECT dept_id, count(*)::int cnt FROM employees WHERE is_active GROUP BY dept_id) c
WHERE c.dept_id = bd.dept_id;

UPDATE positions p
SET headcount_filled = LEAST(c.cnt, p.headcount_budget)::smallint,
    headcount_vacant = GREATEST(p.headcount_budget - c.cnt, 0)::smallint
FROM (SELECT position_id, count(*)::int cnt FROM employees WHERE is_active GROUP BY position_id) c
WHERE c.position_id = p.position_id;

UPDATE positions
SET headcount_vacant = headcount_budget
WHERE headcount_filled = 0;

-- =============================================================================
-- 4. Employee satellites: contacts, documents, contracts, change requests
-- =============================================================================

INSERT INTO emergency_contacts
    (employee_id, full_name, full_name_local, relationship, phone_primary, phone_secondary,
     email, address, national_id, is_primary, created_by, created_at, updated_at)
SELECT e.employee_id,
       (ARRAY['Samir','Nagwa','Hoda','Ashraf','Magdy','Laila'])[1 + pg_temp.h(e.employee_number || 'ec1', 6)]
           || ' ' || e.last_name,
       NULL,
       (ARRAY['Spouse','Father','Mother','Brother','Sister'])[1 + pg_temp.h(e.employee_number || 'rel', 5)],
       '+20 11' || lpad((20000000 + pg_temp.h(e.employee_number, 9000000))::text, 8, '0'),
       NULL,
       NULL,
       e.address_line1 || ', ' || e.city,
       NULL,
       true, 1, now(), now()
FROM employees e
UNION ALL
SELECT e.employee_id,
       (ARRAY['Tamer','Eman','Sherine','Rami'])[1 + pg_temp.h(e.employee_number || 'ec2', 4)]
           || ' ' || e.last_name,
       NULL,
       'Friend',
       '+20 12' || lpad((30000000 + pg_temp.h(e.employee_number || 'b', 9000000))::text, 8, '0'),
       NULL, NULL, NULL, NULL,
       false, 1, now(), now()
FROM employees e
WHERE pg_temp.h(e.employee_number, 10) < 4;

INSERT INTO employee_documents
    (employee_id, document_type, document_category, document_title, document_number,
     issuing_authority, issuing_country, issue_date, expiry_date, alert_days_before,
     file_path, file_type, file_size_kb, is_mandatory, is_verified, verified_by, verified_date,
     is_active, uploaded_at, uploaded_by, created_at, updated_at)
SELECT e.employee_id, d.document_type, d.document_category,
       d.title_prefix || ' - ' || e.first_name || ' ' || e.last_name,
       d.number_prefix || lpad(pg_temp.h(e.employee_number || d.document_type, 900000)::text, 8, '0'),
       d.authority, 'EG',
       (e.hire_date - d.issue_offset)::date,
       CASE WHEN d.validity_days IS NULL THEN NULL
            ELSE (e.hire_date - d.issue_offset + d.validity_days)::date END,
       30,
       '/storage/hr/documents/' || e.employee_number || '/' || d.document_type || '.pdf',
       'pdf',
       120 + pg_temp.h(e.employee_number || d.document_type, 4000),
       d.is_mandatory,
       pg_temp.h(e.employee_number || d.document_type, 10) < 8,
       1, now() - interval '30 days',
       true, now() - interval '40 days', 1, now(), now()
FROM employees e
CROSS JOIN (VALUES
    ('national_id',       'identity',     'National ID Card',      'NID-',  'Civil Status Authority',        400,  NULL,  true),
    ('passport',          'identity',     'Passport',              'PSP-',  'Egyptian Passport Authority',   900,  3650,  false),
    ('degree_certificate','educational',  'Bachelor Degree',       'DEG-',  'Cairo University',              2500, NULL,  true),
    ('work_permit',       'legal',        'Work Permit',           'WPM-',  'Ministry of Manpower',          200,  730,   false)
) AS d(document_type, document_category, title_prefix, number_prefix, authority, issue_offset, validity_days, is_mandatory)
WHERE d.document_type <> 'work_permit' OR pg_temp.h(e.employee_number, 10) < 3;

INSERT INTO employee_contracts
    (employee_id, contract_number, contract_type, branch_id, dept_id, position_id, job_grade_id,
     start_date, end_date, is_open_ended, work_location, work_schedule, weekly_hours,
     probation_days, probation_end_date, notice_period_days, notice_period_unit,
     renewal_type, renewal_count, contract_status, special_conditions, file_path,
     is_current, approved_by, approved_date, created_by, created_at, updated_at)
SELECT e.employee_id,
       'CTR-' || substr(e.employee_number, 5),
       CASE e.employment_type
            WHEN 'internship' THEN 'internship'
            WHEN 'contract'   THEN 'fixed_term'
            WHEN 'part_time'  THEN 'permanent'
            ELSE 'permanent' END,
       e.branch_id, e.dept_id, e.position_id, e.job_grade_id,
       e.hire_date,
       CASE WHEN e.employment_type IN ('internship','contract')
            THEN (e.hire_date + 365)::date END,
       e.employment_type NOT IN ('internship','contract'),
       b.branch_name || ', ' || b.city,
       (ARRAY['standard','hybrid','remote','shift'])[1 + pg_temp.h(e.employee_number || 'ws', 4)],
       CASE WHEN e.employment_type = 'part_time' THEN 20 ELSE 40 END,
       90, (e.hire_date + 90)::date,
       CASE WHEN e.employee_number < 'EMP-0016' THEN 3 ELSE 2 END, 'month',
       CASE WHEN e.employment_type IN ('internship','contract') THEN 'fixed_term_renewal' END,
       0,
       CASE WHEN e.employment_status IN ('terminated','resigned','retired') THEN 'terminated'
            WHEN e.employment_status = 'inactive' THEN 'expired'
            ELSE 'active' END,
       NULL,
       '/storage/hr/contracts/' || e.employee_number || '.pdf',
       true, 1, e.hire_date, 1, now(), now()
FROM employees e
JOIN organization_branches b ON b.branch_id = e.branch_id;

INSERT INTO employee_change_requests
    (employee_id, field_name, old_value, new_value, status, requested_at,
     decided_by, decided_at, decision_note, created_at, updated_at, created_by)
SELECT e.employee_id, c.field_name, c.old_value, c.new_value, c.status,
       now() - (c.days_ago || ' days')::interval,
       CASE WHEN c.status <> 'pending' THEN 1 END,
       CASE WHEN c.status <> 'pending' THEN now() - ((c.days_ago - 2) || ' days')::interval END,
       CASE WHEN c.status = 'approved' THEN 'Supporting document verified.'
            WHEN c.status = 'rejected' THEN 'Supporting document missing.' END,
       now(), now(), 1
FROM employees e
CROSS JOIN LATERAL (VALUES
    ('phone_mobile',  e.phone_mobile, '+20 100 555 ' || substr(e.employee_number, 5),
     (ARRAY['pending','approved','rejected'])[1 + pg_temp.h(e.employee_number || 'cr', 3)], 14),
    ('address_line1', e.address_line1, '77 New Cairo Boulevard',
     (ARRAY['approved','pending'])[1 + pg_temp.h(e.employee_number || 'cr2', 2)], 45)
) AS c(field_name, old_value, new_value, status, days_ago)
WHERE pg_temp.h(e.employee_number, 10) < 5;

-- --- onboarding --------------------------------------------------------------
INSERT INTO onboarding_task_templates (day_milestone, title, description, sort_order, created_at, updated_at, created_by)
VALUES
    (1,  'Collect signed contract and ID documents', 'HR verifies identity documents and files the signed contract.', 1, now(), now(), 1),
    (1,  'Issue laptop and access badge',            'IT provisions hardware; facilities issues the building badge.',  2, now(), now(), 1),
    (1,  'Create system accounts',                   'Email, ERP and VPN accounts created with default role.',         3, now(), now(), 1),
    (30, 'Complete mandatory compliance training',   'Code of conduct and information-security courses.',              4, now(), now(), 1),
    (30, '30-day check-in with line manager',        'Structured conversation on ramp-up and blockers.',               5, now(), now(), 1),
    (90, 'Probation review meeting',                 'Confirm or extend probation based on documented objectives.',    6, now(), now(), 1),
    (90, 'Set first performance goals',              'Employee and manager agree goals for the current cycle.',        7, now(), now(), 1);

INSERT INTO onboarding_task_completions (employee_id, template_id, completed_at, created_at, updated_at)
SELECT e.employee_id, t.template_id,
       (e.hire_date + t.day_milestone)::timestamptz + interval '11 hours',
       now(), now()
FROM employees e
CROSS JOIN onboarding_task_templates t
WHERE e.hire_date + t.day_milestone <= CURRENT_DATE
  AND pg_temp.h(e.employee_number || t.title, 10) < 8;

INSERT INTO employee_export_jobs
    (requested_by, format, scope, filename, status, file_path, error_message, created_at, updated_at)
VALUES
    (1, 'xlsx', 'all',      'employees_full_export.xlsx',      'completed',  '/storage/exports/employees_full_export.xlsx',  NULL,                          now() - interval '9 days',  now() - interval '9 days'),
    (1, 'csv',  'filtered', 'employees_sales_dept.csv',        'completed',  '/storage/exports/employees_sales_dept.csv',    NULL,                          now() - interval '5 days',  now() - interval '5 days'),
    (2, 'pdf',  'selected', 'employees_probation_list.pdf',    'failed',     NULL,                                          'Template rendering timed out.', now() - interval '3 days',  now() - interval '3 days'),
    (2, 'xlsx', 'filtered', 'employees_alexandria_branch.xlsx','processing', NULL,                                          NULL,                          now() - interval '2 hours', now() - interval '1 hour'),
    (3, 'csv',  'all',      'employees_headcount_snapshot.csv','queued',     NULL,                                          NULL,                          now() - interval '20 minutes', now() - interval '20 minutes');

-- =============================================================================
-- 5. Compensation
-- =============================================================================

-- Superseded salary history for a subset of employees.
INSERT INTO salary
    (employee_id, job_grade_id, cost_centre_id, basic_salary, housing_allowance, transport_allowance,
     food_allowance, mobile_allowance, other_allowance, gross_salary, income_tax_pct,
     social_insurance_pct, other_deduction, net_salary, currency_code, pay_frequency, pay_method,
     bank_name, bank_account, bank_iban, effective_date, end_date, change_reason, change_type,
     approval_status, approved_by, approved_date, is_current, created_by, created_at, updated_at)
SELECT e.employee_id, e.job_grade_id, e.cost_centre_id,
       s.basic, s.housing, s.transport, s.food, s.mobile, 0,
       s.gross, 10.00, 11.00, 0,
       round(s.gross * 0.79, 2),
       'EGP', 'monthly', 'bank_transfer',
       'National Bank of Egypt',
       lpad(pg_temp.h(e.employee_number || 'acc', 999999999)::text, 12, '0'),
       'EG38' || lpad(pg_temp.h(e.employee_number || 'iban', 999999999)::text, 25, '0'),
       e.hire_date, (CURRENT_DATE - 366)::date, 'hire', NULL,
       'approved', 'HR Director', e.hire_date, false, 1, now(), now()
FROM employees e
JOIN job_grades g ON g.job_grade_id = e.job_grade_id
CROSS JOIN LATERAL (
    SELECT b.basic,
           round(b.basic * 0.25, 2) AS housing,
           round(b.basic * 0.10, 2) AS transport,
           round(b.basic * 0.05, 2) AS food,
           250::numeric(15,2)       AS mobile,
           round(b.basic * 1.40, 2) + 250 AS gross
    FROM (SELECT round(g.mid_salary * 0.90, 2) AS basic) b
) AS s
WHERE e.hire_date < CURRENT_DATE - 400;

-- Current salary for every employee.
INSERT INTO salary
    (employee_id, job_grade_id, cost_centre_id, basic_salary, housing_allowance, transport_allowance,
     food_allowance, mobile_allowance, other_allowance, gross_salary, income_tax_pct,
     social_insurance_pct, other_deduction, net_salary, currency_code, pay_frequency, pay_method,
     bank_name, bank_account, bank_iban, effective_date, end_date, change_reason, change_type,
     approval_status, approved_by, approved_date, is_current, created_by, created_at, updated_at)
SELECT e.employee_id, e.job_grade_id, e.cost_centre_id,
       s.basic, s.housing, s.transport, s.food, s.mobile, s.other,
       s.gross, 12.50, 11.00, s.other_ded,
       round(s.gross * (1 - 0.235) - s.other_ded, 2),
       'EGP', 'monthly',
       CASE WHEN pg_temp.h(e.employee_number || 'pm', 20) = 0 THEN 'wallet' ELSE 'bank_transfer' END,
       (ARRAY['National Bank of Egypt','Banque Misr','CIB','QNB Alahli'])[1 + pg_temp.h(e.employee_number || 'bank', 4)],
       lpad(pg_temp.h(e.employee_number || 'acc', 999999999)::text, 12, '0'),
       'EG38' || lpad(pg_temp.h(e.employee_number || 'iban', 999999999)::text, 25, '0'),
       GREATEST(e.hire_date, (CURRENT_DATE - 365)::date), NULL,
       CASE WHEN e.hire_date >= CURRENT_DATE - 400 THEN 'hire' ELSE 'annual_review' END,
       CASE WHEN e.hire_date >= CURRENT_DATE - 400 THEN NULL ELSE 'increase' END,
       'approved', 'HR Director', GREATEST(e.hire_date, (CURRENT_DATE - 365)::date),
       true, 1, now(), now()
FROM employees e
JOIN job_grades g ON g.job_grade_id = e.job_grade_id
CROSS JOIN LATERAL (
    SELECT b.basic,
           round(b.basic * 0.25, 2) AS housing,
           round(b.basic * 0.10, 2) AS transport,
           round(b.basic * 0.05, 2) AS food,
           300::numeric(15,2)       AS mobile,
           CASE WHEN pg_temp.h(e.employee_number || 'oa', 4) = 0 THEN round(b.basic * 0.03, 2) ELSE 0 END AS other,
           CASE WHEN pg_temp.h(e.employee_number || 'od', 6) = 0 THEN 500 ELSE 0 END::numeric(15,2) AS other_ded,
           round(b.basic * 1.40, 2) + 300
             + CASE WHEN pg_temp.h(e.employee_number || 'oa', 4) = 0 THEN round(b.basic * 0.03, 2) ELSE 0 END AS gross
    FROM (SELECT round(g.mid_salary * (0.92 + pg_temp.h(e.employee_number || 'sal', 22) / 100.0), 2) AS basic) b
) AS s;

-- --- monthly payroll accounting, last 12 periods -----------------------------
INSERT INTO accounting_salary
    (cost_centre_id, dept_id, branch_id, pay_period, fiscal_year, fiscal_month, currency_code,
     gross_salary_budget, gross_salary_actual, net_salary_actual, allowances_total,
     deductions_total, employer_contrib, posting_status, gl_account_code, journal_ref,
     posted_date, posted_by, created_by, created_at, updated_at)
SELECT cc.cost_centre_id, cc.dept_id, cc.branch_id,
       to_char(m.period, 'YYYY-MM')::char(7),
       EXTRACT(YEAR FROM m.period)::smallint,
       EXTRACT(MONTH FROM m.period)::smallint,
       'EGP',
       round(agg.gross * 1.05, 2),
       agg.gross,
       agg.net,
       agg.allowances,
       round(agg.gross - agg.net, 2),
       round(agg.gross * 0.1875, 2),
       CASE WHEN m.period < date_trunc('month', CURRENT_DATE) - interval '1 month' THEN 'posted'
            WHEN m.period < date_trunc('month', CURRENT_DATE) THEN 'pending'
            ELSE 'draft' END,
       '5' || lpad((EXTRACT(MONTH FROM m.period))::text, 4, '0'),
       'JV-' || to_char(m.period, 'YYYYMM') || '-' || cc.cc_code,
       CASE WHEN m.period < date_trunc('month', CURRENT_DATE) - interval '1 month'
            THEN (m.period + interval '1 month' + interval '4 days')::date END,
       CASE WHEN m.period < date_trunc('month', CURRENT_DATE) - interval '1 month'
            THEN 'Finance Controller' END,
       1, now(), now()
FROM cost_centers cc
CROSS JOIN LATERAL generate_series(
    date_trunc('month', CURRENT_DATE) - interval '11 months',
    date_trunc('month', CURRENT_DATE),
    interval '1 month') AS m(period)
CROSS JOIN LATERAL (
    SELECT COALESCE(sum(s.gross_salary), 0)::numeric(15,2) AS gross,
           COALESCE(sum(s.net_salary), 0)::numeric(15,2)   AS net,
           COALESCE(sum(s.housing_allowance + s.transport_allowance
                        + s.food_allowance + s.mobile_allowance + s.other_allowance), 0)::numeric(15,2) AS allowances
    FROM salary s
    JOIN employees e ON e.employee_id = s.employee_id
    WHERE s.is_current AND e.cost_centre_id = cc.cost_centre_id
) AS agg;

-- --- payslips ----------------------------------------------------------------
INSERT INTO ps_payslip_documents
    (employee_id, period, pdf_path, gross, total_deductions, net, status, published_at,
     created_by, created_at, updated_at)
SELECT e.employee_id,
       to_char(m.period, 'YYYY-MM'),
       '/storage/hr/payslips/' || e.employee_number || '/' || to_char(m.period, 'YYYY-MM') || '.pdf',
       round(s.gross_salary, 2),
       round(s.gross_salary - s.net_salary, 2),
       round(s.net_salary, 2),
       CASE WHEN m.period < date_trunc('month', CURRENT_DATE) THEN 'sent' ELSE 'generated' END,
       CASE WHEN m.period < date_trunc('month', CURRENT_DATE)
            THEN (m.period + interval '1 month' + interval '2 days')::timestamptz END,
       1, now(), now()
FROM employees e
JOIN salary s ON s.employee_id = e.employee_id AND s.is_current
CROSS JOIN LATERAL generate_series(
    date_trunc('month', CURRENT_DATE) - interval '7 months',
    date_trunc('month', CURRENT_DATE),
    interval '1 month') AS m(period)
WHERE e.is_active
  AND m.period >= date_trunc('month', e.hire_date::timestamp);

INSERT INTO ps_payslip_access_log (payslip_id, accessed_by, action, ip, accessed_at)
SELECT p.payslip_id,
       1000 + pg_temp.h(p.payslip_id::text, 500),
       CASE WHEN pg_temp.h(p.payslip_id::text || 'a', 3) = 0 THEN 'downloaded' ELSE 'viewed' END,
       '10.20.' || pg_temp.h(p.payslip_id::text, 250) || '.' || pg_temp.h(p.payslip_id::text || 'ip', 250),
       p.published_at + interval '2 days'
FROM ps_payslip_documents p
WHERE p.published_at IS NOT NULL
  AND pg_temp.h(p.payslip_id::text, 10) < 6;

-- --- loans, bonuses, expense claims, payroll injections ----------------------
INSERT INTO pg_loans
    (loan_no, employee_id, type, principal_amount, installments_count, installment_amount,
     remaining_amount, reason, status, start_period, created_by, created_at, updated_at)
SELECT 'LN-' || to_char(CURRENT_DATE, 'YYYY') || '-' || lpad(row_number() OVER (ORDER BY e.employee_number)::text, 4, '0'),
       e.employee_id,
       CASE WHEN pg_temp.h(e.employee_number || 'ln', 3) = 0 THEN 'advance' ELSE 'loan' END,
       amt.principal,
       amt.installments,
       round(amt.principal / amt.installments, 2),
       round(amt.principal / amt.installments, 2) * (amt.installments - amt.paid),
       (ARRAY['Home renovation','Medical expenses','Family emergency','Education fees'])[1 + pg_temp.h(e.employee_number || 'lr', 4)],
       (ARRAY['active','active','approved','settled','pending_approval'])[1 + pg_temp.h(e.employee_number || 'ls', 5)],
       to_char(date_trunc('month', CURRENT_DATE) - interval '5 months', 'YYYY-MM'),
       1, now(), now()
FROM employees e
CROSS JOIN LATERAL (
    SELECT ((1 + pg_temp.h(e.employee_number || 'lp', 8)) * 5000)::numeric(12,2) AS principal,
           (6 + pg_temp.h(e.employee_number || 'li', 7)) AS installments,
           (1 + pg_temp.h(e.employee_number || 'lpa', 4)) AS paid
) AS amt
WHERE e.is_active AND pg_temp.h(e.employee_number, 10) < 3;

INSERT INTO pg_loan_schedules (loan_id, period, amount, status, created_at, updated_at)
SELECT l.loan_id,
       to_char(date_trunc('month', to_date(l.start_period, 'YYYY-MM')) + (i || ' months')::interval, 'YYYY-MM'),
       l.installment_amount,
       CASE WHEN date_trunc('month', to_date(l.start_period, 'YYYY-MM')) + (i || ' months')::interval
                 < date_trunc('month', CURRENT_DATE) THEN 'deducted'
            WHEN pg_temp.h(l.loan_no || i::text, 20) = 0 THEN 'skipped'
            ELSE 'scheduled' END,
       now(), now()
FROM pg_loans l
CROSS JOIN generate_series(0, 11) AS i
WHERE i < l.installments_count;

INSERT INTO pg_bonuses (employee_id, type, amount, period, status, note, created_by, created_at, updated_at)
SELECT e.employee_id,
       (ARRAY['performance','spot','eid','other'])[1 + pg_temp.h(e.employee_number || 'bt', 4)],
       ((2 + pg_temp.h(e.employee_number || 'ba', 12)) * 1000)::numeric(12,2),
       to_char(date_trunc('month', CURRENT_DATE) - ((1 + pg_temp.h(e.employee_number || 'bp', 4)) || ' months')::interval, 'YYYY-MM'),
       (ARRAY['injected','approved','approved','pending_approval','rejected'])[1 + pg_temp.h(e.employee_number || 'bs', 5)],
       (ARRAY['Exceeded quarterly target','Outstanding customer feedback','Annual Eid bonus','Project delivery recognition'])[1 + pg_temp.h(e.employee_number || 'bn', 4)],
       1, now(), now()
FROM employees e
WHERE e.is_active AND pg_temp.h(e.employee_number || 'bonus', 10) < 6;

INSERT INTO pg_expense_claims
    (claim_no, employee_id, category, amount, currency_code, incurred_on, receipt_path,
     status, payout_method, created_by, created_at, updated_at)
SELECT 'EXP-' || to_char(CURRENT_DATE, 'YYYY') || '-' || lpad(row_number() OVER (ORDER BY e.employee_number, c.idx)::text, 5, '0'),
       e.employee_id,
       (ARRAY['travel','meals','medical','transport','other'])[1 + pg_temp.h(e.employee_number || c.idx::text, 5)],
       ((1 + pg_temp.h(e.employee_number || 'ea' || c.idx::text, 40)) * 75)::numeric(12,2),
       'EGP',
       (CURRENT_DATE - (5 + pg_temp.h(e.employee_number || 'ed' || c.idx::text, 120)))::date,
       '/storage/hr/receipts/' || e.employee_number || '-' || c.idx || '.pdf',
       (ARRAY['paid','approved','pending_approval','rejected'])[1 + pg_temp.h(e.employee_number || 'es' || c.idx::text, 4)],
       CASE WHEN pg_temp.h(e.employee_number || 'pm' || c.idx::text, 4) = 0 THEN 'petty_cash' ELSE 'payroll' END,
       1, now(), now()
FROM employees e
CROSS JOIN generate_series(1, 2) AS c(idx)
WHERE e.is_active AND pg_temp.h(e.employee_number || 'exp', 10) < 5;

INSERT INTO pg_payroll_injections
    (source_type, source_id, employee_id, period, direction, amount, status, synced_at, created_at, updated_at)
SELECT 'loan', l.loan_id, l.employee_id, s.period, 'deduction', s.amount,
       CASE WHEN s.status = 'deducted' THEN 'sent' ELSE 'pending' END,
       CASE WHEN s.status = 'deducted' THEN now() - interval '20 days' END,
       now(), now()
FROM pg_loan_schedules s
JOIN pg_loans l ON l.loan_id = s.loan_id
WHERE s.status IN ('deducted', 'scheduled')
UNION ALL
SELECT 'bonus', b.bonus_id, b.employee_id, b.period, 'addition', b.amount,
       CASE WHEN b.status = 'injected' THEN 'included' ELSE 'pending' END,
       CASE WHEN b.status = 'injected' THEN now() - interval '15 days' END,
       now(), now()
FROM pg_bonuses b
WHERE b.status IN ('injected', 'approved')
UNION ALL
SELECT 'expense', c.claim_id, c.employee_id,
       to_char(c.incurred_on, 'YYYY-MM'), 'addition', c.amount,
       CASE WHEN c.status = 'paid' THEN 'sent' ELSE 'pending' END,
       CASE WHEN c.status = 'paid' THEN now() - interval '10 days' END,
       now(), now()
FROM pg_expense_claims c
WHERE c.status IN ('paid', 'approved')
  AND c.payout_method = 'payroll';

-- =============================================================================
-- 6. Working calendar and leave
-- =============================================================================

INSERT INTO working_day_calendars
    (calendar_date, is_working_day, day_type, description, country_code, created_at, updated_at, created_by)
SELECT d::date,
       CASE WHEN EXTRACT(DOW FROM d) IN (5, 6) THEN false
            WHEN hol.label IS NOT NULL THEN false
            ELSE true END,
       CASE WHEN EXTRACT(DOW FROM d) IN (5, 6) THEN 'weekend'
            WHEN hol.label IS NOT NULL THEN 'public_holiday'
            ELSE 'weekday' END,
       hol.label,
       'EGY', now(), now(), 1
FROM generate_series(CURRENT_DATE - 450, CURRENT_DATE + 120, interval '1 day') AS d
LEFT JOIN LATERAL (
    SELECT CASE
        WHEN to_char(d, 'MM-DD') = '01-01' THEN 'New Year''s Day'
        WHEN to_char(d, 'MM-DD') = '01-25' THEN 'Revolution Day / National Police Day'
        WHEN to_char(d, 'MM-DD') = '04-25' THEN 'Sinai Liberation Day'
        WHEN to_char(d, 'MM-DD') = '05-01' THEN 'Labour Day'
        WHEN to_char(d, 'MM-DD') = '06-30' THEN 'June 30 Revolution Day'
        WHEN to_char(d, 'MM-DD') = '07-23' THEN 'Revolution Day'
        WHEN to_char(d, 'MM-DD') = '10-06' THEN 'Armed Forces Day'
    END AS label
) AS hol ON true;

INSERT INTO leave_types
    (leave_code, leave_name, leave_name_local, category, is_paid, requires_document,
     affects_attendance, color_hex, created_by, created_at, updated_at)
VALUES
    ('ANN', 'Annual Leave',        'إجازة سنوية',     'annual',       true,  false, true,  '#6366F1', 1, now(), now()),
    ('SCK', 'Sick Leave',          'إجازة مرضية',     'sick',         true,  true,  true,  '#EF4444', 1, now(), now()),
    ('MAT', 'Maternity Leave',     'إجازة وضع',       'maternity',    true,  true,  true,  '#EC4899', 1, now(), now()),
    ('PAT', 'Paternity Leave',     'إجازة أبوة',      'paternity',    true,  false, true,  '#0EA5E9', 1, now(), now()),
    ('UNP', 'Unpaid Leave',        'إجازة بدون أجر',  'unpaid',       false, false, true,  '#6B7280', 1, now(), now()),
    ('EMG', 'Emergency Leave',     'إجازة طارئة',     'emergency',    true,  false, true,  '#F59E0B', 1, now(), now()),
    ('BRV', 'Bereavement Leave',   'إجازة وفاة',      'bereavement',  true,  true,  true,  '#334155', 1, now(), now()),
    ('HAJ', 'Hajj Leave',          'إجازة حج',        'hajj',         true,  true,  true,  '#10B981', 1, now(), now());

INSERT INTO leave_policies
    (leave_type_id, scope, scope_id, annual_entitlement_days, accrual_method, accrual_rate,
     max_carry_forward_days, min_service_months, max_consecutive_days, notice_days,
     allow_negative_balance, gender_eligibility, created_by, created_at, updated_at)
-- Company-wide baselines
SELECT lt.leave_type_id, 'all', NULL, p.days, p.method, p.rate, p.carry, p.min_months, p.max_consec, p.notice,
       false, p.gender, 1, now(), now()
FROM (VALUES
    ('ANN', 21.0, 'monthly',     1.75, 5.0,  6,  15, 7,  'all'),
    ('SCK', 12.0, 'annual_lump', NULL, 0.0,  0,  10, 0,  'all'),
    ('MAT', 90.0, 'none',        NULL, NULL, 10, 90, 30, 'female'),
    ('PAT',  3.0, 'none',        NULL, NULL, 0,  3,  3,  'male'),
    ('UNP', 30.0, 'none',        NULL, NULL, 12, 30, 14, 'all'),
    ('EMG',  5.0, 'annual_lump', NULL, NULL, 0,  2,  0,  'all'),
    ('BRV',  3.0, 'annual_lump', NULL, NULL, 0,  3,  0,  'all'),
    ('HAJ', 21.0, 'none',        NULL, NULL, 60, 21, 60, 'all')
) AS p(code, days, method, rate, carry, min_months, max_consec, notice, gender)
JOIN leave_types lt ON lt.leave_code = p.code
UNION ALL
-- Enhanced annual leave for senior grades
SELECT lt.leave_type_id, 'grade', g.job_grade_id, g.annual_leave_days::numeric(5,1), 'monthly',
       round(g.annual_leave_days / 12.0, 2), 10.0, 0, 20, 7, false, 'all', 1, now(), now()
FROM job_grades g
JOIN leave_types lt ON lt.leave_code = 'ANN'
WHERE g.grade_code IN ('G1','G2','G3');

-- Balances for the current and previous fiscal year.
INSERT INTO leave_balances
    (employee_id, leave_type_id, fiscal_year, opening_balance, accrued_ytd, used_ytd, pending_ytd,
     created_at, updated_at, created_by)
SELECT e.employee_id, lt.leave_type_id, fy.year,
       CASE WHEN fy.year = EXTRACT(YEAR FROM CURRENT_DATE)::smallint
            THEN LEAST(pg_temp.h(e.employee_number || lt.leave_code, 6), 5)::numeric(7,2)
            ELSE 0 END,
       CASE lt.leave_code
            WHEN 'ANN' THEN LEAST(g.annual_leave_days, 21)::numeric(7,2)
            WHEN 'SCK' THEN g.sick_leave_days::numeric(7,2)
            WHEN 'EMG' THEN 5.00
            WHEN 'BRV' THEN 3.00
            ELSE 0 END,
       0, 0,
       now(), now(), 1
FROM employees e
JOIN job_grades g ON g.job_grade_id = e.job_grade_id
CROSS JOIN leave_types lt
CROSS JOIN (VALUES
    ((EXTRACT(YEAR FROM CURRENT_DATE) - 1)::smallint),
    (EXTRACT(YEAR FROM CURRENT_DATE)::smallint)
) AS fy(year)
WHERE lt.leave_code IN ('ANN','SCK','EMG','BRV','UNP')
  AND EXTRACT(YEAR FROM e.hire_date) <= fy.year;

-- Leave requests: a realistic mix of statuses spread over the last year.
INSERT INTO leave_requests
    (employee_id, leave_type_id, start_date, end_date, working_days, is_half_day, half_day_period,
     reason, medical_certificate_path, medical_certificate_mime, status,
     reviewed_by_emp_id, reviewed_at, review_notes, created_by, created_at, updated_at)
SELECT e.employee_id, lt.leave_type_id,
       req.start_date,
       (req.start_date + req.span)::date,
       (req.span + 1)::numeric(5,2),
       false, NULL,
       (ARRAY['Family trip','Personal matters','Medical appointment','Rest and recovery','Religious observance'])[1 + pg_temp.h(e.employee_number || i::text, 5)],
       CASE WHEN lt.leave_code = 'SCK'
            THEN '/storage/hr/medical/' || e.employee_number || '-' || i || '.pdf' END,
       CASE WHEN lt.leave_code = 'SCK' THEN 'application/pdf' END,
       req.status,
       CASE WHEN req.status IN ('approved','rejected') THEN e.manager_id END,
       CASE WHEN req.status IN ('approved','rejected')
            THEN (req.start_date - 3)::timestamptz + interval '10 hours' END,
       CASE WHEN req.status = 'approved' THEN 'Approved; coverage arranged.'
            WHEN req.status = 'rejected' THEN 'Rejected; conflicts with a critical delivery.' END,
       1, now(), now()
FROM employees e
CROSS JOIN generate_series(1, 4) AS i
CROSS JOIN LATERAL (
    SELECT (ARRAY['ANN','ANN','SCK','EMG'])[i] AS code
) AS pick
JOIN leave_types lt ON lt.leave_code = pick.code
CROSS JOIN LATERAL (
    SELECT (CURRENT_DATE - (330 - i * 70 - pg_temp.h(e.employee_number || 'ls' || i::text, 40)))::date AS start_date,
           pg_temp.h(e.employee_number || 'sp' || i::text, 4) AS span,
           CASE
               WHEN pg_temp.h(e.employee_number || 'st' || i::text, 10) < 7 THEN 'approved'
               WHEN pg_temp.h(e.employee_number || 'st' || i::text, 10) = 7 THEN 'pending'
               WHEN pg_temp.h(e.employee_number || 'st' || i::text, 10) = 8 THEN 'rejected'
               ELSE 'cancelled'
           END AS status
) AS req
WHERE e.is_active
  AND req.start_date > e.hire_date
  AND pg_temp.h(e.employee_number || 'lv' || i::text, 10) < 7;

-- Reconcile balances against the requests just created.
UPDATE leave_balances lb
SET used_ytd = COALESCE(u.used, 0),
    pending_ytd = COALESCE(u.pending, 0)
FROM (
    SELECT lr.employee_id, lr.leave_type_id,
           EXTRACT(YEAR FROM lr.start_date)::smallint AS fy,
           sum(lr.working_days) FILTER (WHERE lr.status = 'approved') AS used,
           sum(lr.working_days) FILTER (WHERE lr.status = 'pending')  AS pending
    FROM leave_requests lr
    GROUP BY 1, 2, 3
) u
WHERE u.employee_id = lb.employee_id
  AND u.leave_type_id = lb.leave_type_id
  AND u.fy = lb.fiscal_year;

-- Monthly accrual logs for annual leave in the current fiscal year.
INSERT INTO leave_accrual_logs
    (employee_id, leave_type_id, balance_id, accrual_date, days_accrued,
     balance_before, balance_after, accrual_formula, notes, run_id, created_at)
SELECT lb.employee_id, lb.leave_type_id, lb.balance_id,
       m.d::date,
       1.75,
       round(lb.opening_balance + 1.75 * (mn.rn - 1), 2),
       round(lb.opening_balance + 1.75 * mn.rn, 2),
       'monthly',
       'Automatic monthly accrual run.',
       run.run_id,
       m.d::timestamptz
FROM leave_balances lb
JOIN leave_types lt ON lt.leave_type_id = lb.leave_type_id AND lt.leave_code = 'ANN'
CROSS JOIN LATERAL generate_series(
    date_trunc('year', CURRENT_DATE),
    date_trunc('month', CURRENT_DATE),
    interval '1 month') WITH ORDINALITY AS m(d, rn)
CROSS JOIN LATERAL (SELECT m.rn AS rn) mn
CROSS JOIN LATERAL (
    SELECT ('00000000-0000-4000-8000-' || lpad(EXTRACT(MONTH FROM m.d)::text, 12, '0'))::uuid AS run_id
) run
WHERE lb.fiscal_year = EXTRACT(YEAR FROM CURRENT_DATE)::smallint;

INSERT INTO leave_adjustments
    (employee_id, leave_type_id, fiscal_year, adjustment_days, reason, adjusted_by, created_at)
SELECT lb.employee_id, lb.leave_type_id, lb.fiscal_year,
       CASE WHEN pg_temp.h(lb.balance_id::text, 2) = 0 THEN 2.00 ELSE -1.00 END,
       CASE WHEN pg_temp.h(lb.balance_id::text, 2) = 0
            THEN 'Carry-forward correction approved by HR.'
            ELSE 'Correction of a duplicated leave deduction.' END,
       1, now() - interval '25 days'
FROM leave_balances lb
JOIN leave_types lt ON lt.leave_type_id = lb.leave_type_id AND lt.leave_code = 'ANN'
WHERE lb.fiscal_year = EXTRACT(YEAR FROM CURRENT_DATE)::smallint
  AND pg_temp.h(lb.balance_id::text || 'adj', 10) < 3;

-- =============================================================================
-- 7. Time & attendance
-- =============================================================================

INSERT INTO work_shifts
    (shift_code, shift_name, shift_name_local, start_time, end_time, grace_period_minutes,
     break_duration_minutes, overtime_threshold_minutes, geofence_latitude, geofence_longitude,
     geofence_radius_meters, is_night_shift, created_by, created_at, updated_at)
VALUES
    ('GEN',   'General Day Shift',  'الوردية العامة',   '09:00', '17:00', 15, 60, 60, 30.0444196, 31.2357116, 250, false, 1, now(), now()),
    ('EARLY', 'Early Shift',        'الوردية المبكرة',  '07:00', '15:00', 10, 45, 60, 30.0444196, 31.2357116, 250, false, 1, now(), now()),
    ('LATE',  'Late Shift',         'الوردية المتأخرة', '13:00', '21:00', 15, 45, 60, 31.2000924, 29.9187387, 300, false, 1, now(), now()),
    ('NIGHT', 'Night Shift',        'الوردية الليلية',  '22:00', '06:00', 20, 60, 45, 29.9602800, 30.9214000, 400, true,  1, now(), now());

INSERT INTO shift_assignments
    (employee_id, shift_id, effective_from, effective_to, assigned_by, created_at, updated_at, created_by)
SELECT e.employee_id, ws.shift_id, e.hire_date, NULL, 1, now(), now(), 1
FROM employees e
JOIN work_shifts ws ON ws.shift_code =
    CASE WHEN e.dept_id = (SELECT dept_id FROM branch_departments WHERE dept_code = 'OPS')
              AND pg_temp.h(e.employee_number || 'sh', 3) = 0 THEN 'NIGHT'
         WHEN e.dept_id = (SELECT dept_id FROM branch_departments WHERE dept_code = 'CS')
              AND pg_temp.h(e.employee_number || 'sh', 2) = 0 THEN 'LATE'
         WHEN pg_temp.h(e.employee_number || 'sh', 8) = 0 THEN 'EARLY'
         ELSE 'GEN' END;

-- Daily attendance for the last 120 days: on-time, late, early departure,
-- overtime, unexcused absence and implicit absence (no record at all).
INSERT INTO attendance_records
    (employee_id, attendance_date, shift_id, clock_in_at, clock_out_at,
     clock_in_source, clock_out_source, clock_in_location, clock_out_location,
     late_minutes, early_departure_minutes, hours_worked, overtime_minutes, notes,
     created_at, updated_at, created_by)
SELECT e.employee_id, c.calendar_date, sa.shift_id,
       CASE WHEN v.absent THEN NULL
            ELSE (c.calendar_date + ws.start_time)::timestamptz + make_interval(mins => v.late) END,
       CASE WHEN v.absent THEN NULL
            ELSE (c.calendar_date + ws.end_time)::timestamptz
                 + make_interval(mins => v.overtime - v.early)
                 + CASE WHEN ws.is_night_shift THEN interval '1 day' ELSE interval '0' END END,
       (ARRAY['web','mobile','kiosk'])[1 + pg_temp.h(e.employee_number || c.calendar_date::text || 'src', 3)],
       CASE WHEN v.absent THEN NULL
            ELSE (ARRAY['web','mobile','kiosk'])[1 + pg_temp.h(e.employee_number || c.calendar_date::text || 'osrc', 3)] END,
       CASE WHEN v.absent THEN NULL
            ELSE jsonb_build_object('lat', ws.geofence_latitude, 'lng', ws.geofence_longitude, 'accuracy_m', 8) END,
       CASE WHEN v.absent THEN NULL
            ELSE jsonb_build_object('lat', ws.geofence_latitude, 'lng', ws.geofence_longitude, 'accuracy_m', 11) END,
       v.late, v.early,
       CASE WHEN v.absent THEN 0
            ELSE GREATEST(round(8 - v.late / 60.0 - v.early / 60.0 + v.overtime / 60.0, 2), 0) END,
       v.overtime,
       CASE WHEN v.absent THEN 'Unexcused absence - no clock-in recorded.'
            WHEN v.late > 30 THEN 'Late arrival reported to line manager.' END,
       now(), now(), 1
FROM employees e
JOIN shift_assignments sa ON sa.employee_id = e.employee_id
JOIN work_shifts ws ON ws.shift_id = sa.shift_id
CROSS JOIN working_day_calendars c
CROSS JOIN LATERAL (
    SELECT r.r,
           (r.r < 4) AS absent,
           CASE WHEN r.r BETWEEN 4 AND 19 THEN (5 + (r.r * 7) % 55)::smallint ELSE 0::smallint END AS late,
           CASE WHEN r.r BETWEEN 20 AND 26 THEN (10 + (r.r * 3) % 40)::smallint ELSE 0::smallint END AS early,
           CASE WHEN r.r >= 92 THEN (30 + (r.r % 7) * 15)::smallint ELSE 0::smallint END AS overtime
    FROM (SELECT pg_temp.h(e.employee_number || c.calendar_date::text, 100) AS r) r
) AS v
WHERE e.employment_status IN ('active','probation','on_leave','suspended')
  AND c.is_working_day
  AND c.calendar_date BETWEEN GREATEST(e.hire_date, CURRENT_DATE - 120) AND CURRENT_DATE - 1
  AND pg_temp.h(e.employee_number || c.calendar_date::text || 'present', 100) < 92
  AND NOT EXISTS (
      SELECT 1 FROM leave_requests lr
      WHERE lr.employee_id = e.employee_id
        AND lr.status = 'approved'
        AND c.calendar_date BETWEEN lr.start_date AND lr.end_date
  );

INSERT INTO attendance_summaries
    (employee_id, year, month, working_days_in_month, days_present, days_absent, days_on_leave,
     total_hours_worked, total_late_minutes, total_overtime_minutes, total_early_departure_minutes,
     created_at, updated_at, created_by)
SELECT a.employee_id, a.y, a.m,
       wd.working_days,
       a.present, a.absent,
       COALESCE(lv.leave_days, 0),
       a.hours, a.late, a.overtime, a.early,
       now(), now(), 1
FROM (
    SELECT ar.employee_id,
           EXTRACT(YEAR FROM ar.attendance_date)::smallint  AS y,
           EXTRACT(MONTH FROM ar.attendance_date)::smallint AS m,
           count(*) FILTER (WHERE ar.clock_in_at IS NOT NULL)::smallint AS present,
           count(*) FILTER (WHERE ar.clock_in_at IS NULL)::smallint     AS absent,
           sum(ar.hours_worked)::numeric(6,2)                           AS hours,
           sum(ar.late_minutes)::int                                    AS late,
           sum(ar.overtime_minutes)::int                                AS overtime,
           sum(ar.early_departure_minutes)::int                         AS early
    FROM attendance_records ar
    GROUP BY 1, 2, 3
) a
JOIN LATERAL (
    SELECT count(*)::smallint AS working_days
    FROM working_day_calendars c
    WHERE c.is_working_day
      AND EXTRACT(YEAR FROM c.calendar_date) = a.y
      AND EXTRACT(MONTH FROM c.calendar_date) = a.m
) wd ON true
LEFT JOIN LATERAL (
    SELECT count(*)::smallint AS leave_days
    FROM leave_requests lr
    CROSS JOIN LATERAL generate_series(lr.start_date, lr.end_date, interval '1 day') AS d
    JOIN working_day_calendars wc ON wc.calendar_date = d::date AND wc.is_working_day
    WHERE lr.employee_id = a.employee_id
      AND lr.status = 'approved'
      AND EXTRACT(YEAR FROM d) = a.y
      AND EXTRACT(MONTH FROM d) = a.m
) lv ON true;

INSERT INTO overtime_requests
    (employee_id, attendance_record_id, overtime_date, requested_minutes, approved_minutes,
     reason, status, reviewed_by_emp_id, reviewed_at, review_notes, created_at, updated_at, created_by)
SELECT ar.employee_id, ar.record_id, ar.attendance_date,
       ar.overtime_minutes,
       CASE WHEN st.status = 'approved' THEN ar.overtime_minutes END,
       (ARRAY['Month-end closing','Customer escalation','Production deployment','Stock count'])[1 + pg_temp.h(ar.record_id::text, 4)],
       st.status,
       CASE WHEN st.status <> 'pending' THEN e.manager_id END,
       CASE WHEN st.status <> 'pending' THEN ar.attendance_date::timestamptz + interval '2 days' END,
       CASE WHEN st.status = 'approved' THEN 'Approved for payroll injection.'
            WHEN st.status = 'rejected' THEN 'Not pre-authorised.' END,
       now(), now(), 1
FROM attendance_records ar
JOIN employees e ON e.employee_id = ar.employee_id
CROSS JOIN LATERAL (
    SELECT (ARRAY['approved','approved','approved','pending','rejected'])[1 + pg_temp.h(ar.record_id::text || 'ot', 5)] AS status
) st
WHERE ar.overtime_minutes > 0;

-- =============================================================================
-- 8. Performance management
-- =============================================================================

INSERT INTO review_cycles
    (cycle_name, cycle_name_local, cycle_type, period_start, period_end, phases_json,
     rating_scale, status, created_by, created_at, updated_at)
VALUES
    ('Annual Review ' || (EXTRACT(YEAR FROM CURRENT_DATE) - 1)::text,
     'المراجعة السنوية', 'annual',
     (date_trunc('year', CURRENT_DATE) - interval '1 year')::date,
     (date_trunc('year', CURRENT_DATE) - interval '1 day')::date,
     '{"self":"2025-11-01","manager":"2025-11-20","calibration":"2025-12-05"}'::jsonb,
     5, 'closed', 1, now(), now()),
    ('Annual Review ' || EXTRACT(YEAR FROM CURRENT_DATE)::text,
     'المراجعة السنوية', 'annual',
     date_trunc('year', CURRENT_DATE)::date,
     (date_trunc('year', CURRENT_DATE) + interval '1 year' - interval '1 day')::date,
     '{"self":"2026-11-01","manager":"2026-11-20","calibration":"2026-12-05"}'::jsonb,
     5, 'active', 1, now(), now()),
    ('H1 ' || EXTRACT(YEAR FROM CURRENT_DATE)::text || ' Mid-Year Review',
     'مراجعة منتصف العام', 'semi_annual',
     date_trunc('year', CURRENT_DATE)::date,
     (date_trunc('year', CURRENT_DATE) + interval '6 months' - interval '1 day')::date,
     '{"self":"2026-06-15","manager":"2026-06-30"}'::jsonb,
     5, 'manager_review', 1, now(), now());

INSERT INTO cycle_participants
    (cycle_id, employee_id, reviewer_emp_id, phase_status, self_done_at, manager_done_at,
     created_at, updated_at, created_by)
SELECT rc.cycle_id, e.employee_id, e.manager_id,
       ph.phase_status,
       CASE WHEN ph.phase_status <> 'pending' THEN now() - interval '40 days' END,
       CASE WHEN ph.phase_status IN ('manager_done','calibrated','finalised') THEN now() - interval '25 days' END,
       now(), now(), 1
FROM review_cycles rc
CROSS JOIN employees e
CROSS JOIN LATERAL (
    SELECT CASE WHEN rc.status = 'closed' THEN 'finalised'
                ELSE (ARRAY['pending','self_in_progress','self_done','manager_in_progress','manager_done'])
                     [1 + pg_temp.h(e.employee_number || rc.cycle_name, 5)]
           END AS phase_status
) ph
WHERE e.is_active
  AND e.hire_date <= rc.period_start;

INSERT INTO appraisals
    (cycle_id, employee_id, reviewer_emp_id, self_score, manager_score, final_score, status,
     self_submitted_at, manager_submitted_at, acknowledged_at, created_by, created_at, updated_at)
SELECT cp.cycle_id, cp.employee_id, cp.reviewer_emp_id,
       sc.self_score, sc.manager_score,
       CASE WHEN rc.status = 'closed' THEN round((sc.self_score + sc.manager_score * 3) / 4, 2) END,
       CASE WHEN rc.status = 'closed' THEN 'acknowledged'
            ELSE (ARRAY['pending','self_in_progress','self_submitted','manager_in_progress','manager_submitted'])
                 [1 + pg_temp.h(cp.participant_id::text, 5)] END,
       cp.self_done_at, cp.manager_done_at,
       CASE WHEN rc.status = 'closed' THEN now() - interval '20 days' END,
       1, now(), now()
FROM cycle_participants cp
JOIN review_cycles rc ON rc.cycle_id = cp.cycle_id
CROSS JOIN LATERAL (
    SELECT round(3.0 + pg_temp.h(cp.participant_id::text || 'self', 20) / 10.0, 2)::numeric(5,2) AS self_score,
           round(2.8 + pg_temp.h(cp.participant_id::text || 'mgr', 22) / 10.0, 2)::numeric(5,2)  AS manager_score
) sc;

INSERT INTO competencies
    (competency_name, competency_name_local, grade_scope, description, created_by, created_at, updated_at)
VALUES
    ('Customer Focus',        'التركيز على العميل',    NULL, 'Anticipates and responds to internal and external customer needs.', 1, now(), now()),
    ('Collaboration',         'العمل الجماعي',         NULL, 'Works effectively across teams and functions.',                     1, now(), now()),
    ('Ownership',             'تحمل المسؤولية',        NULL, 'Takes end-to-end responsibility for outcomes.',                     1, now(), now()),
    ('Analytical Thinking',   'التفكير التحليلي',      NULL, 'Breaks down complex problems and reasons from data.',               1, now(), now()),
    ('Communication',         'التواصل',               NULL, 'Communicates clearly in Arabic and English.',                       1, now(), now()),
    ('People Leadership',     'قيادة الفريق',          NULL, 'Develops, coaches and retains team members.',                       1, now(), now());

UPDATE competencies c
SET grade_scope = g.job_grade_id
FROM job_grades g
WHERE g.grade_code = 'G4' AND c.competency_name = 'People Leadership';

INSERT INTO appraisal_ratings
    (appraisal_id, item_type, item_id, weight_pct, self_rating, manager_rating,
     self_comment, manager_comment, created_at, updated_at)
SELECT a.appraisal_id, 'competency', c.competency_id,
       round(100.0 / 6, 2),
       round(3.0 + pg_temp.h(a.appraisal_id::text || c.competency_name, 20) / 10.0, 2),
       round(2.8 + pg_temp.h(a.appraisal_id::text || c.competency_name || 'm', 22) / 10.0, 2),
       'Consistently demonstrated this competency during the period.',
       'Agreed; keep developing depth in the second half.',
       now(), now()
FROM appraisals a
CROSS JOIN competencies c
WHERE a.status IN ('manager_submitted','finalised','acknowledged','calibrated');

INSERT INTO calibration_sessions
    (cycle_id, scope, scope_id, status, facilitator_emp_id, finalised_at, created_by, created_at, updated_at)
SELECT rc.cycle_id, 'dept', bd.dept_id,
       CASE WHEN rc.status = 'closed' THEN 'finalised' ELSE 'in_progress' END,
       bd.manager_emp_id,
       CASE WHEN rc.status = 'closed' THEN now() - interval '30 days' END,
       1, now(), now()
FROM review_cycles rc
CROSS JOIN branch_departments bd
WHERE rc.cycle_type = 'annual'
  AND bd.dept_code IN ('SLS','ENG','OPS','CS');

INSERT INTO calibration_entries
    (session_id, employee_id, original_rating, calibrated_rating, performance_axis,
     potential_axis, box_position, notes, created_at, updated_at, created_by)
SELECT cs.session_id, e.employee_id,
       a.manager_score,
       round(a.manager_score + (pg_temp.h(e.employee_number || cs.session_id::text, 3) - 1) * 0.2, 2),
       round(a.manager_score, 2),
       round(2.5 + pg_temp.h(e.employee_number || 'pot', 25) / 10.0, 2),
       (1 + pg_temp.h(e.employee_number || 'box', 9))::smallint,
       'Discussed in the departmental calibration session.',
       now(), now(), 1
FROM calibration_sessions cs
JOIN employees e ON e.dept_id = cs.scope_id AND e.is_active
JOIN appraisals a ON a.employee_id = e.employee_id AND a.cycle_id = cs.cycle_id;

INSERT INTO feedback_requests
    (cycle_id, subject_emp_id, rater_emp_id, relationship, status, sent_at, responded_at,
     created_at, updated_at, created_by)
SELECT rc.cycle_id, e.employee_id, peer.employee_id,
       CASE WHEN peer.employee_id = e.manager_id THEN 'manager' ELSE 'peer' END,
       st.status,
       now() - interval '35 days',
       CASE WHEN st.status = 'responded' THEN now() - interval '28 days' END,
       now(), now(), 1
FROM review_cycles rc
JOIN employees e ON e.is_active
JOIN LATERAL (
    SELECT p.employee_id
    FROM employees p
    WHERE p.dept_id = e.dept_id
      AND p.employee_id <> e.employee_id
      AND p.is_active
    ORDER BY p.employee_number
    LIMIT 2
) peer ON true
CROSS JOIN LATERAL (
    SELECT (ARRAY['responded','responded','sent','declined','expired'])
           [1 + pg_temp.h(e.employee_number || peer.employee_id::text, 5)] AS status
) st
WHERE rc.status = 'active';

INSERT INTO feedback_responses
    (request_id, ratings_json, strengths, improvements, is_anonymous, submitted_at,
     created_at, updated_at, created_by)
SELECT fr.request_id,
       jsonb_build_object(
           'collaboration', 3 + pg_temp.h(fr.request_id::text || 'c', 3),
           'communication', 3 + pg_temp.h(fr.request_id::text || 'm', 3),
           'ownership',     3 + pg_temp.h(fr.request_id::text || 'o', 3)),
       'Reliable under pressure and quick to help teammates.',
       'Could share written updates earlier in the sprint.',
       pg_temp.h(fr.request_id::text || 'anon', 3) = 0,
       fr.responded_at, now(), now(), 1
FROM feedback_requests fr
WHERE fr.status = 'responded';

INSERT INTO goals
    (employee_id, title, title_local, description, goal_type, category, weight_pct,
     cycle_id, start_date, due_date, progress_pct, status, created_by, created_at, updated_at)
SELECT e.employee_id,
       g.title, g.title_local, g.description, g.goal_type, g.category, g.weight_pct,
       rc.cycle_id,
       rc.period_start,
       rc.period_end,
       LEAST(round(pg_temp.h(e.employee_number || g.title, 101)::numeric, 2), 100),
       (ARRAY['active','active','completed','approved','submitted'])[1 + pg_temp.h(e.employee_number || g.title || 's', 5)],
       1, now(), now()
FROM employees e
JOIN review_cycles rc ON rc.status = 'active'
CROSS JOIN (VALUES
    ('Hit the annual revenue target',        'تحقيق هدف الإيرادات السنوي', 'Close the assigned pipeline for the fiscal year.', 'okr',   'business', 40.00),
    ('Improve process cycle time by 15%',    'تقليل زمن الدورة 15%',        'Reduce hand-off delays in the core workflow.',      'smart', 'operational', 30.00),
    ('Complete the mandatory training plan', 'إتمام خطة التدريب الإلزامية', 'Finish compliance and role-specific courses.',      'smart', 'development', 30.00)
) AS g(title, title_local, description, goal_type, category, weight_pct)
WHERE e.is_active;

INSERT INTO goal_checkins (goal_id, progress_pct, comment, created_by, created_at, updated_at)
SELECT g.goal_id,
       LEAST(round(g.progress_pct * (0.4 + i * 0.3), 2), 100),
       (ARRAY['On track after the quarterly review.','Blocked briefly by a dependency, now resolved.','Ahead of plan.'])[i],
       1,
       (g.start_date + (i * 60))::timestamptz,
       now()
FROM goals g
CROSS JOIN generate_series(1, 3) AS i
WHERE g.start_date + (i * 60) <= CURRENT_DATE;

INSERT INTO key_results
    (goal_id, description, metric_type, currency_code, start_value, target_value,
     current_value, progress_pct, created_at, updated_at, created_by)
SELECT g.goal_id, kr.description, kr.metric_type, 'EGP',
       kr.start_value, kr.target_value,
       round(kr.start_value + (kr.target_value - kr.start_value) * g.progress_pct / 100.0, 2),
       g.progress_pct,
       now(), now(), 1
FROM goals g
CROSS JOIN LATERAL (VALUES
    ('Signed contract value for the year', 'currency',   0::numeric(15,2), 4000000::numeric(15,2)),
    ('Average cycle time in days',         'number',     18::numeric(15,2),   12::numeric(15,2)),
    ('Training completion rate',           'percentage',  0::numeric(15,2),  100::numeric(15,2))
) AS kr(description, metric_type, start_value, target_value)
WHERE g.goal_type = 'okr';

INSERT INTO kpi_library
    (kpi_code, kpi_name, kpi_name_local, category, unit, direction, measurement_type,
     formula, data_source, description, created_by, created_at, updated_at)
VALUES
    ('KPI-REV',  'Revenue Achievement',      'تحقيق الإيرادات',        'sales',      'EGP',   'higher_better', 'currency',   'sum(closed_won_value)',                    'sales.invoices',        'Total closed revenue against target.',            1, now(), now()),
    ('KPI-ATT',  'Attendance Rate',          'معدل الحضور',            'hr',         '%',     'higher_better', 'percentage', 'days_present / working_days * 100',        'hr.attendance_records', 'Share of working days actually attended.',        1, now(), now()),
    ('KPI-LATE', 'Punctuality',              'الالتزام بالمواعيد',      'hr',         'min',   'lower_better',  'number',     'sum(late_minutes)',                        'hr.attendance_records', 'Accumulated lateness in minutes.',                1, now(), now()),
    ('KPI-TRN',  'Training Completion',      'إتمام التدريب',          'learning',   '%',     'higher_better', 'percentage', 'completed_courses / assigned_courses * 100','hr.program_enrollments','Mandatory training completion rate.',             1, now(), now()),
    ('KPI-TTH',  'Time to Hire',             'زمن التوظيف',            'recruitment','days',  'lower_better',  'number',     'avg(offer_date - requisition_open_date)',  'hr.rec_applications',   'Average days from requisition to accepted offer.',1, now(), now()),
    ('KPI-TURN', 'Voluntary Turnover',       'معدل الاستقالات',        'hr',         '%',     'lower_better',  'percentage', 'resignations / headcount * 100',           'hr.employees',          'Voluntary leavers as a share of headcount.',      1, now(), now());

INSERT INTO kpi_assignments
    (kpi_id, assignee_type, assignee_id, cycle_id, weight_pct, target_value, currency_code,
     threshold_min, threshold_max, period, status, created_by, created_at, updated_at)
SELECT k.kpi_id, 'dept', bd.dept_id, rc.cycle_id,
       round(100.0 / 3, 2),
       CASE k.kpi_code WHEN 'KPI-REV' THEN 4000000 WHEN 'KPI-ATT' THEN 95 ELSE 90 END,
       'EGP',
       CASE k.kpi_code WHEN 'KPI-REV' THEN 3000000 ELSE 80 END,
       CASE k.kpi_code WHEN 'KPI-REV' THEN 5000000 ELSE 100 END,
       to_char(CURRENT_DATE, 'YYYY-MM'),
       'active', 1, now(), now()
FROM kpi_library k
CROSS JOIN branch_departments bd
JOIN review_cycles rc ON rc.status = 'active'
WHERE k.kpi_code IN ('KPI-REV','KPI-ATT','KPI-TRN')
UNION ALL
SELECT k.kpi_id, 'employee', e.employee_id, rc.cycle_id,
       50.00, 95, 'EGP', 85, 100, to_char(CURRENT_DATE, 'YYYY-MM'),
       'active', 1, now(), now()
FROM kpi_library k
CROSS JOIN employees e
JOIN review_cycles rc ON rc.status = 'active'
WHERE k.kpi_code = 'KPI-ATT' AND e.is_active;

INSERT INTO improvement_plans
    (employee_id, cycle_id, reason, objectives_json, start_date, end_date, status,
     manager_emp_id, hr_emp_id, outcome, created_by, created_at, updated_at)
SELECT e.employee_id, rc.cycle_id,
       'Performance below the agreed expectations for two consecutive quarters.',
       jsonb_build_array(
           jsonb_build_object('objective', 'Meet 90% of the agreed delivery milestones', 'due_weeks', 6),
           jsonb_build_object('objective', 'Complete the coaching plan with the line manager', 'due_weeks', 8),
           jsonb_build_object('objective', 'Reduce accumulated lateness to under 60 minutes per month', 'due_weeks', 4)),
       (CURRENT_DATE - 70)::date, (CURRENT_DATE + 20)::date,
       (ARRAY['active','monitoring','completed','failed'])[1 + pg_temp.h(e.employee_number || 'pip', 4)],
       e.manager_id,
       (SELECT employee_id FROM employees WHERE employee_number = 'EMP-0002'),
       NULL, 1, now(), now()
FROM employees e
JOIN review_cycles rc ON rc.status = 'active'
WHERE e.is_active AND pg_temp.h(e.employee_number || 'pipsel', 12) = 0;

-- =============================================================================
-- 9. Learning: skills, courses, programmes, sessions, certifications
-- =============================================================================

INSERT INTO skills (skill_code, skill_name, skill_name_local, category, description, created_by, created_at, updated_at)
VALUES
    ('SK-SQL',   'SQL',                  'SQL',                 'technical',   'Relational querying and data modelling.',   1, now(), now()),
    ('SK-PY',    'Python',               'بايثون',              'technical',   'Scripting and automation.',                 1, now(), now()),
    ('SK-ERP',   'ERP Administration',   'إدارة أنظمة ERP',     'technical',   'Configuring and operating the ERP suite.',  1, now(), now()),
    ('SK-NEG',   'Negotiation',          'التفاوض',             'soft_skills', 'Commercial negotiation and closing.',        1, now(), now()),
    ('SK-COM',   'Business Communication','التواصل المهني',     'soft_skills', 'Written and verbal business communication.',1, now(), now()),
    ('SK-LEAD',  'Team Leadership',      'قيادة الفرق',         'leadership',  'Leading and developing a team.',            1, now(), now()),
    ('SK-EXCEL', 'Advanced Excel',       'إكسل متقدم',          'technical',   'Modelling, pivots and Power Query.',        1, now(), now()),
    ('SK-SAFE',  'Workplace Safety',     'السلامة المهنية',     'compliance',  'Occupational health and safety rules.',     1, now(), now()),
    ('SK-GDPR',  'Data Protection',      'حماية البيانات',      'compliance',  'Personal data handling obligations.',       1, now(), now()),
    ('SK-CS',    'Customer Service',     'خدمة العملاء',        'soft_skills', 'Handling customer issues end to end.',      1, now(), now());

INSERT INTO role_skill_requirements (job_grade_id, skill_id, required_level, created_by)
SELECT g.job_grade_id, s.skill_id,
       LEAST(GREATEST(g.grade_level - 3, 1), 5)::smallint,
       1
FROM job_grades g
CROSS JOIN skills s
WHERE (g.grade_code IN ('G1','G2','G3','G4') AND s.skill_code IN ('SK-LEAD','SK-COM','SK-NEG'))
   OR (g.grade_code IN ('G5','G6','G7','G8') AND s.skill_code IN ('SK-EXCEL','SK-COM','SK-SAFE'));

INSERT INTO employee_skills
    (employee_id, skill_id, current_level, target_level, assessed_by, assessed_at, source, created_at, updated_at)
SELECT e.employee_id, s.skill_id,
       (1 + pg_temp.h(e.employee_number || s.skill_code, 5))::smallint,
       LEAST((1 + pg_temp.h(e.employee_number || s.skill_code, 5)) + 1, 5)::smallint,
       e.manager_id,
       now() - interval '60 days',
       (ARRAY['self','manager','assessment','training','certification'])[1 + pg_temp.h(e.employee_number || s.skill_code || 'src', 5)],
       now(), now()
FROM employees e
CROSS JOIN skills s
WHERE pg_temp.h(e.employee_number || s.skill_code || 'has', 10) < 5;

INSERT INTO training_courses
    (course_code, course_name, course_name_local, category, delivery_mode, level, duration_hours,
     provider, cost_per_seat, currency_code, passing_score, validity_months, is_mandatory,
     created_by, created_at, updated_at)
VALUES
    ('CRS-ONB',  'New Joiner Orientation',      'توجيه الموظفين الجدد',   'onboarding',  'blended',    'beginner',     8.00, 'Internal L&D',        0.00,    'EGP', NULL,  NULL, true,  1, now(), now()),
    ('CRS-SAFE', 'Workplace Safety Essentials', 'أساسيات السلامة المهنية','safety',      'classroom',  'beginner',     6.00, 'SafetyFirst Egypt',   1200.00, 'EGP', 70.00, 12,   true,  1, now(), now()),
    ('CRS-GDPR', 'Data Protection & Privacy',   'حماية البيانات والخصوصية','compliance', 'e_learning', 'beginner',     4.00, 'Internal L&D',        0.00,    'EGP', 80.00, 12,   true,  1, now(), now()),
    ('CRS-SQL',  'SQL for Business Analysis',   'SQL لتحليل الأعمال',     'technical',   'online',     'intermediate',16.00, 'DataCraft Academy',   3500.00, 'EGP', 65.00, 24,   false, 1, now(), now()),
    ('CRS-ERP',  'ERP Power User',              'المستخدم المتقدم لـ ERP','technical',   'blended',    'intermediate',24.00, 'Internal L&D',        2000.00, 'EGP', 70.00, 24,   false, 1, now(), now()),
    ('CRS-NEG',  'Consultative Selling',        'البيع الاستشاري',        'soft_skills', 'classroom',  'intermediate',12.00, 'SalesEdge MENA',      4800.00, 'EGP', 60.00, NULL, false, 1, now(), now()),
    ('CRS-LEAD', 'First-Time Manager',          'المدير لأول مرة',        'leadership',  'blended',    'advanced',     20.00,'LeadWell Consulting', 7500.00, 'EGP', 70.00, 36,   false, 1, now(), now()),
    ('CRS-CS',   'Service Excellence',          'التميز في الخدمة',       'product',     'online',     'beginner',     6.00, 'Internal L&D',        0.00,    'EGP', 60.00, 12,   false, 1, now(), now());

INSERT INTO course_modules (course_id, sequence_order, title, description, material_url, duration_minutes, created_by)
SELECT c.course_id, m.seq,
       c.course_name || ' - Module ' || m.seq,
       'Module ' || m.seq || ' of the ' || c.course_name || ' curriculum.',
       '/storage/lms/' || c.course_code || '/module-' || m.seq || '.pdf',
       (c.duration_hours * 60 / 4)::int,
       1
FROM training_courses c
CROSS JOIN generate_series(1, 4) AS m(seq);

INSERT INTO course_prerequisites (course_id, prerequisite_course_id, created_by)
SELECT adv.course_id, base.course_id, 1
FROM (VALUES
    ('CRS-ERP',  'CRS-ONB'),
    ('CRS-LEAD', 'CRS-ONB'),
    ('CRS-NEG',  'CRS-CS')
) AS p(course_code, prereq_code)
JOIN training_courses adv  ON adv.course_code  = p.course_code
JOIN training_courses base ON base.course_code = p.prereq_code;

INSERT INTO training_programs
    (program_code, program_name, program_name_local, category, description, status, provider,
     owner_emp_id, target_audience, total_duration_hours, cost_per_seat, currency_code,
     max_cohort_size, start_date, end_date, created_by, created_at, updated_at)
SELECT p.program_code, p.program_name, p.program_name_local, p.category, p.description,
       p.status, p.provider,
       (SELECT employee_id FROM employees WHERE employee_number = 'EMP-0002'),
       p.target_audience, p.total_hours, p.cost, 'EGP', p.max_size,
       (CURRENT_DATE - p.start_offset)::date,
       (CURRENT_DATE - p.start_offset + 180)::date,
       1, now(), now()
FROM (VALUES
    ('PRG-ONB',  'Onboarding Journey',        'رحلة الانضمام',       'onboarding', 'Structured 90-day onboarding for all new joiners.', 'active', 'Internal L&D', 'All new joiners',        18.00, 0.00,     40, 300),
    ('PRG-COMP', 'Annual Compliance Program', 'برنامج الالتزام السنوي','compliance','Mandatory yearly compliance refreshers.',           'active', 'Internal L&D', 'All employees',          10.00, 1200.00,  60, 200),
    ('PRG-LEAD', 'Emerging Leaders',          'القادة الصاعدون',     'leadership', 'Development track for high-potential managers.',     'active', 'LeadWell Consulting','Managers and senior ICs', 32.00, 9500.00, 20, 150),
    ('PRG-SALE', 'Sales Excellence',          'التميز في المبيعات',  'soft_skills','Commercial capability uplift for the sales team.',   'paused', 'SalesEdge MENA','Sales department',        18.00, 4800.00,  25, 120)
) AS p(program_code, program_name, program_name_local, category, description, status, provider,
       target_audience, total_hours, cost, max_size, start_offset);

INSERT INTO program_courses (program_id, course_id, sequence_order, is_mandatory, created_by)
SELECT p.program_id, c.course_id, m.seq, m.mandatory, 1
FROM training_programs p
JOIN LATERAL (VALUES
    ('PRG-ONB',  'CRS-ONB',  1::smallint, true),
    ('PRG-ONB',  'CRS-GDPR', 2::smallint, true),
    ('PRG-COMP', 'CRS-GDPR', 1::smallint, true),
    ('PRG-COMP', 'CRS-SAFE', 2::smallint, true),
    ('PRG-LEAD', 'CRS-LEAD', 1::smallint, true),
    ('PRG-LEAD', 'CRS-SQL',  2::smallint, false),
    ('PRG-SALE', 'CRS-NEG',  1::smallint, true),
    ('PRG-SALE', 'CRS-CS',   2::smallint, false)
) AS m(program_code, course_code, seq, mandatory) ON m.program_code = p.program_code
JOIN training_courses c ON c.course_code = m.course_code;

INSERT INTO program_cohorts
    (program_id, cohort_name, start_date, end_date, capacity, enrolled_count, status, created_by)
SELECT p.program_id,
       p.program_code || '-C' || i,
       (p.start_date + (i - 1) * 60)::date,
       (p.start_date + (i - 1) * 60 + 45)::date,
       p.max_cohort_size,
       0,
       CASE WHEN p.start_date + (i - 1) * 60 + 45 < CURRENT_DATE THEN 'completed'
            WHEN p.start_date + (i - 1) * 60 <= CURRENT_DATE THEN 'in_progress'
            ELSE 'open' END,
       1
FROM training_programs p
CROSS JOIN generate_series(1, 2) AS i;

INSERT INTO program_enrollments
    (program_id, cohort_id, employee_id, status, enrolled_at, completion_pct, completed_at, created_at, updated_at)
SELECT pc.program_id, pc.cohort_id, e.employee_id,
       st.status,
       pc.start_date::timestamptz,
       st.pct,
       CASE WHEN st.status = 'completed' THEN (pc.end_date)::timestamptz END,
       now(), now()
FROM employees e
JOIN LATERAL (
    SELECT c.program_id, c.cohort_id, c.start_date, c.end_date
    FROM program_cohorts c
    JOIN training_programs p ON p.program_id = c.program_id
    WHERE p.program_code = 'PRG-ONB'
    ORDER BY c.cohort_name
    LIMIT 1
) pc ON true
CROSS JOIN LATERAL (
    SELECT CASE WHEN pg_temp.h(e.employee_number || 'pe', 10) < 6 THEN 'completed'
                WHEN pg_temp.h(e.employee_number || 'pe', 10) < 8 THEN 'in_progress'
                WHEN pg_temp.h(e.employee_number || 'pe', 10) = 8 THEN 'enrolled'
                ELSE 'dropped' END AS status,
           CASE WHEN pg_temp.h(e.employee_number || 'pe', 10) < 6 THEN 100.00
                WHEN pg_temp.h(e.employee_number || 'pe', 10) < 8 THEN 45.00
                WHEN pg_temp.h(e.employee_number || 'pe', 10) = 8 THEN 0.00
                ELSE 20.00 END AS pct
) st
WHERE e.is_active;

UPDATE program_cohorts pc
SET enrolled_count = c.cnt
FROM (SELECT cohort_id, count(*)::int cnt FROM program_enrollments WHERE cohort_id IS NOT NULL GROUP BY cohort_id) c
WHERE c.cohort_id = pc.cohort_id;

INSERT INTO training_sessions
    (course_id, cohort_id, start_datetime, end_datetime, trainer_emp_id, external_trainer,
     venue, delivery_mode, max_seats, enrolled_count, waitlist_count, status,
     created_by, created_at, updated_at)
SELECT c.course_id, NULL,
       (CURRENT_DATE - 90 + s.offset_days)::timestamptz + interval '9 hours',
       (CURRENT_DATE - 90 + s.offset_days)::timestamptz + interval '9 hours' + (c.duration_hours || ' hours')::interval,
       CASE WHEN c.provider = 'Internal L&D'
            THEN (SELECT employee_id FROM employees WHERE employee_number = 'EMP-0006') END,
       CASE WHEN c.provider <> 'Internal L&D' THEN c.provider || ' - Lead Trainer' END,
       CASE WHEN c.delivery_mode IN ('online','e_learning') THEN 'Virtual classroom'
            ELSE 'Cairo Head Office, Training Room ' || (1 + s.offset_days % 3) END,
       c.delivery_mode,
       20, 0, 0,
       CASE WHEN CURRENT_DATE - 90 + s.offset_days < CURRENT_DATE THEN 'completed' ELSE 'scheduled' END,
       1, now(), now()
FROM training_courses c
CROSS JOIN (VALUES (0), (35), (75), (115)) AS s(offset_days);

INSERT INTO session_enrollments
    (session_id, employee_id, status, waitlist_position, enrolled_at, created_at, updated_at)
SELECT ts.session_id, e.employee_id,
       CASE WHEN ts.status = 'completed'
            THEN (ARRAY['attended','attended','attended','no_show','cancelled'])[1 + pg_temp.h(e.employee_number || ts.session_id::text, 5)]
            ELSE (ARRAY['enrolled','approved','requested','waitlisted'])[1 + pg_temp.h(e.employee_number || ts.session_id::text, 4)]
       END,
       NULL,
       ts.start_datetime - interval '10 days',
       now(), now()
FROM training_sessions ts
JOIN employees e ON e.is_active
WHERE pg_temp.h(e.employee_number || ts.session_id::text || 'enr', 100) < 18;

UPDATE session_enrollments se
SET waitlist_position = 1 + pg_temp.h(se.enrollment_id::text, 5)
WHERE se.status = 'waitlisted';

UPDATE training_sessions ts
SET enrolled_count = c.enrolled,
    waitlist_count = c.waitlisted
FROM (
    SELECT session_id,
           count(*) FILTER (WHERE status IN ('enrolled','approved','attended'))::int AS enrolled,
           count(*) FILTER (WHERE status = 'waitlisted')::int                        AS waitlisted
    FROM session_enrollments GROUP BY session_id
) c
WHERE c.session_id = ts.session_id;

INSERT INTO session_attendances
    (session_id, employee_id, attended, score, result, marked_by, marked_at, created_by)
SELECT se.session_id, se.employee_id,
       se.status = 'attended',
       CASE WHEN se.status = 'attended'
            THEN round(55 + pg_temp.h(se.enrollment_id::text || 'sc', 45)::numeric, 2) END,
       CASE WHEN se.status <> 'attended' THEN 'pending'
            WHEN 55 + pg_temp.h(se.enrollment_id::text || 'sc', 45) >= 70 THEN 'pass'
            ELSE 'fail' END,
       (SELECT employee_id FROM employees WHERE employee_number = 'EMP-0006'),
       ts.end_datetime + interval '1 day',
       1
FROM session_enrollments se
JOIN training_sessions ts ON ts.session_id = se.session_id
WHERE ts.status = 'completed'
  AND se.status IN ('attended','no_show');

INSERT INTO training_feedbacks
    (session_id, employee_id, content_rating, trainer_rating, relevance_rating,
     would_recommend, comments, submitted_at)
SELECT sa.session_id, sa.employee_id,
       (3 + pg_temp.h(sa.attendance_id::text || 'c', 3))::smallint,
       (3 + pg_temp.h(sa.attendance_id::text || 't', 3))::smallint,
       (3 + pg_temp.h(sa.attendance_id::text || 'r', 3))::smallint,
       pg_temp.h(sa.attendance_id::text || 'w', 10) < 8,
       (ARRAY['Very practical and well paced.','Good content, would like more hands-on labs.','Useful but too long for the material covered.'])[1 + pg_temp.h(sa.attendance_id::text || 'cm', 3)],
       sa.marked_at + interval '2 days'
FROM session_attendances sa
WHERE sa.attended;

INSERT INTO certifications
    (employee_id, course_id, cert_name, cert_name_local, issuing_body, obtained_date,
     expiry_date, status, certificate_url, created_by, created_at, updated_at)
SELECT sa.employee_id, ts.course_id,
       c.course_name || ' Certificate',
       c.course_name_local,
       COALESCE(c.provider, 'Internal L&D'),
       ts.end_datetime::date,
       CASE WHEN c.validity_months IS NOT NULL
            THEN (ts.end_datetime::date + (c.validity_months || ' months')::interval)::date END,
       CASE WHEN c.validity_months IS NULL THEN 'active'
            WHEN ts.end_datetime::date + (c.validity_months || ' months')::interval < CURRENT_DATE THEN 'expired'
            WHEN ts.end_datetime::date + (c.validity_months || ' months')::interval < CURRENT_DATE + 60 THEN 'expiring_soon'
            ELSE 'active' END,
       '/storage/lms/certificates/' || sa.attendance_id || '.pdf',
       1, now(), now()
FROM session_attendances sa
JOIN training_sessions ts ON ts.session_id = sa.session_id
JOIN training_courses c ON c.course_id = ts.course_id
WHERE sa.result = 'pass';

-- =============================================================================
-- 10. Recruitment
-- =============================================================================

INSERT INTO rec_job_requisitions
    (requisition_no, job_id, dept_id, hiring_manager_id, headcount, filled_count, status,
     visibility, location_label, employment_type, opened_at, closed_at, created_by, created_at, updated_at)
SELECT 'REQ-' || to_char(CURRENT_DATE, 'YYYY') || '-' || lpad(row_number() OVER (ORDER BY bd.dept_code)::text, 3, '0'),
       jc.job_id, bd.dept_id, bd.manager_emp_id,
       1 + pg_temp.h(bd.dept_code || 'hc', 3),
       0,
       (ARRAY['open','open','open','pending_approval','closed','on_hold'])[1 + pg_temp.h(bd.dept_code || 'st', 6)],
       (ARRAY['external','both','internal'])[1 + pg_temp.h(bd.dept_code || 'vis', 3)],
       b.city || ', Egypt',
       'full_time',
       now() - ((30 + pg_temp.h(bd.dept_code, 90)) || ' days')::interval,
       NULL,
       1, now(), now()
FROM branch_departments bd
JOIN organization_branches b ON b.branch_id = bd.branch_id
JOIN job_catalog jc ON jc.job_code = 'SPC-' || bd.dept_code
WHERE bd.dept_code IN ('SLS','ENG','OPS','CS','FIN','IT','MKT','QA');

UPDATE rec_job_requisitions
SET closed_at = opened_at + interval '55 days'
WHERE status = 'closed';

INSERT INTO rec_pipeline_stages
    (requisition_id, name_ar, name_en, sequence_order, stage_type, is_terminal, created_at, updated_at, created_by)
SELECT r.requisition_id, s.name_ar, s.name_en, s.seq, s.stage_type, s.is_terminal, now(), now(), 1
FROM rec_job_requisitions r
CROSS JOIN (VALUES
    ('الفرز',            'Screening',        1, 'screening',  false),
    ('المقابلة الفنية',  'Technical Interview',2,'interview',  false),
    ('التقييم',          'Assessment',       3, 'assessment', false),
    ('العرض الوظيفي',    'Offer',            4, 'offer',      false),
    ('تم التعيين',       'Hired',            5, 'hired',      true),
    ('مرفوض',            'Rejected',         6, 'rejected',   true)
) AS s(name_ar, name_en, seq, stage_type, is_terminal);

INSERT INTO rec_candidates
    (full_name, email, phone, source, referred_by_employee_id, cv_path, current_title,
     total_experience_years, gdpr_consent_at, created_at, updated_at, created_by)
SELECT c.full_name,
       lower(replace(c.full_name, ' ', '.')) || n || '@candidate-mail.com',
       '+20 15' || lpad((40000000 + n * 71317)::text, 8, '0'),
       (ARRAY['portal','linkedin','referral','agency','walk_in','internal'])[1 + (n % 6)],
       CASE WHEN (n % 6) = 2
            THEN (SELECT employee_id FROM employees ORDER BY employee_number LIMIT 1 OFFSET (n % 40)) END,
       '/storage/recruitment/cv/cand-' || n || '.pdf',
       (ARRAY['Sales Executive','Software Engineer','Operations Coordinator','Support Agent','Financial Analyst','QA Engineer'])[1 + (n % 6)],
       round((1 + (n % 12))::numeric, 1),
       now() - ((10 + n) || ' days')::interval,
       now(), now(), 1
FROM generate_series(1, 40) AS n
CROSS JOIN LATERAL (
    SELECT (ARRAY['Kareem','Nourhan','Islam','Salma','Bassant','Zeyad','Marwan','Habiba','Seif','Farida'])[1 + (n % 10)]
           || ' ' ||
           (ARRAY['Elsayed','Abdelrahman','Fouad','Roushdy','Naguib','Halim','Sorour','Wagdy'])[1 + (n % 8)] AS full_name
) AS c;

INSERT INTO rec_applications
    (requisition_id, candidate_id, current_stage_id, status, rejection_reason, score,
     applied_at, cover_note, created_at, updated_at, created_by)
SELECT r.requisition_id, c.candidate_id, st.stage_id,
       app.status,
       CASE WHEN app.status = 'rejected' THEN
            (ARRAY['Insufficient relevant experience','Salary expectation out of band','Withdrew after the technical stage'])
            [1 + pg_temp.h(c.candidate_id::text || 'rej', 3)] END,
       round((5 + pg_temp.h(c.candidate_id::text || r.requisition_no, 50) / 10.0)::numeric, 1),
       r.opened_at + interval '5 days',
       'Interested in the role and familiar with the industry.',
       now(), now(), 1
FROM rec_candidates c
JOIN LATERAL (
    SELECT r2.requisition_id, r2.requisition_no, r2.opened_at
    FROM rec_job_requisitions r2
    ORDER BY md5(r2.requisition_no || c.candidate_id::text)
    LIMIT 1
) r ON true
CROSS JOIN LATERAL (
    SELECT (ARRAY['active','active','rejected','withdrawn','hired'])
           [1 + pg_temp.h(c.candidate_id::text || 'as', 5)] AS status
) app
JOIN LATERAL (
    SELECT s.stage_id
    FROM rec_pipeline_stages s
    WHERE s.requisition_id = r.requisition_id
      AND s.stage_type = CASE app.status
                             WHEN 'hired'     THEN 'hired'
                             WHEN 'rejected'  THEN 'rejected'
                             WHEN 'withdrawn' THEN 'screening'
                             ELSE (ARRAY['screening','interview','assessment','offer'])
                                  [1 + pg_temp.h(c.candidate_id::text || 'stg', 4)]
                         END
    LIMIT 1
) st ON true;

INSERT INTO rec_interviews
    (application_id, stage_id, interviewer_id, scheduled_at, mode, status, feedback, rating,
     created_at, updated_at, created_by)
SELECT a.application_id, s.stage_id, r.hiring_manager_id,
       a.applied_at + ((7 + pg_temp.h(a.application_id::text, 14)) || ' days')::interval,
       (ARRAY['onsite','online','phone'])[1 + pg_temp.h(a.application_id::text || 'md', 3)],
       iv.status,
       CASE WHEN iv.status = 'done'
            THEN 'Solid fundamentals; some gaps in hands-on depth were noted.' END,
       CASE WHEN iv.status = 'done' THEN (1 + pg_temp.h(a.application_id::text || 'rt', 5)) END,
       now(), now(), 1
FROM rec_applications a
JOIN rec_job_requisitions r ON r.requisition_id = a.requisition_id
JOIN rec_pipeline_stages s ON s.requisition_id = a.requisition_id AND s.stage_type = 'interview'
CROSS JOIN LATERAL (
    SELECT (ARRAY['done','done','scheduled','no_show','cancelled'])
           [1 + pg_temp.h(a.application_id::text || 'iv', 5)] AS status
) iv
WHERE a.status IN ('active','hired','rejected');

INSERT INTO rec_offers
    (application_id, offered_grade_id, gross_salary, currency_code, start_date, status,
     expires_at, accepted_at, created_at, updated_at, created_by)
SELECT a.application_id, g.job_grade_id,
       round(g.mid_salary * 1.40, 2),
       'EGP',
       (CURRENT_DATE + 21 + pg_temp.h(a.application_id::text || 'sd', 30))::date,
       CASE WHEN a.status = 'hired' THEN 'accepted'
            ELSE (ARRAY['sent','approved','pending_approval','declined','expired'])
                 [1 + pg_temp.h(a.application_id::text || 'os', 5)] END,
       now() + interval '14 days',
       CASE WHEN a.status = 'hired' THEN now() - interval '5 days' END,
       now(), now(), 1
FROM rec_applications a
JOIN job_grades g ON g.grade_code = 'G6'
WHERE a.status = 'hired'
   OR pg_temp.h(a.application_id::text || 'hasoffer', 10) < 3;

UPDATE rec_job_requisitions r
SET filled_count = LEAST(c.cnt, r.headcount)
FROM (
    SELECT a.requisition_id, count(*)::int cnt
    FROM rec_applications a WHERE a.status = 'hired' GROUP BY a.requisition_id
) c
WHERE c.requisition_id = r.requisition_id;

-- =============================================================================
-- 11. Employee operations (transfers, promotions, terminations) and settlements
-- =============================================================================

INSERT INTO employee_operations
    (employee_id, operation_type, operation_status, effective_date, reason, changes_applied,
     warnings, next_steps, origin_branch_id, origin_dept_id, origin_position_id,
     origin_job_grade_id, origin_cost_centre_id, origin_manager_id,
     target_branch_id, target_dept_id, target_position_id, target_job_grade_id,
     target_cost_centre_id, target_manager_id, new_basic_salary,
     executed_by, executed_at, created_at, updated_at, notice_period_served, is_active)
-- Promotions
SELECT e.employee_id, 'promotion', 'executed',
       (CURRENT_DATE - 120)::date,
       'Promotion approved following the annual review.',
       jsonb_build_object('job_grade_id', tg.job_grade_id, 'basic_salary_increase_pct', 12),
       '[]'::jsonb,
       jsonb_build_array('Issue an updated contract annex', 'Update payroll from the effective date'),
       e.branch_id, e.dept_id, e.position_id, e.job_grade_id, e.cost_centre_id, e.manager_id,
       e.branch_id, e.dept_id, e.position_id, tg.job_grade_id, e.cost_centre_id, e.manager_id,
       round(s.basic_salary * 1.12, 2),
       1, now() - interval '120 days', now(), now(), NULL::boolean, true
FROM employees e
JOIN salary s ON s.employee_id = e.employee_id AND s.is_current
JOIN job_grades cg ON cg.job_grade_id = e.job_grade_id
JOIN job_grades tg ON tg.grade_level = cg.grade_level + 1
WHERE e.is_active AND pg_temp.h(e.employee_number || 'promo', 12) = 0
UNION ALL
-- Transfers
SELECT e.employee_id, 'transfer', 'executed',
       (CURRENT_DATE - 200)::date,
       'Internal transfer to balance departmental workload.',
       jsonb_build_object('dept_id', e.dept_id, 'branch_id', e.branch_id),
       jsonb_build_array('Employee retains the current job grade'),
       jsonb_build_array('Update the seating plan', 'Reassign open tickets'),
       e.branch_id, e.dept_id, e.position_id, e.job_grade_id, e.cost_centre_id, e.manager_id,
       e.branch_id, e.dept_id, e.position_id, e.job_grade_id, e.cost_centre_id, e.manager_id,
       NULL,
       1, now() - interval '200 days', now(), now(), NULL::boolean, true
FROM employees e
WHERE e.is_active AND pg_temp.h(e.employee_number || 'trans', 14) = 0
UNION ALL
-- Terminations / resignations / retirements
SELECT e.employee_id, 'termination', 'executed',
       e.termination_date,
       e.termination_reason,
       jsonb_build_object('employment_status', e.employment_status, 'termination_type', e.termination_type),
       jsonb_build_array('Final settlement pending finance approval'),
       jsonb_build_array('Revoke system access', 'Collect company assets', 'Issue an experience letter'),
       e.branch_id, e.dept_id, e.position_id, e.job_grade_id, e.cost_centre_id, e.manager_id,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       1, e.termination_date::timestamptz, now(), now(),
       e.termination_type <> 'termination', true
FROM employees e
WHERE e.termination_date IS NOT NULL;

-- Apply the promotion outcome so the data stays internally consistent.
UPDATE employees e
SET job_grade_id = op.target_job_grade_id
FROM employee_operations op
WHERE op.employee_id = e.employee_id
  AND op.operation_type = 'promotion'
  AND op.operation_status = 'executed';

INSERT INTO tal_role_highlights (operation_id, highlight, sort_order, created_at, updated_at, created_by)
SELECT op.operation_id, h.highlight, h.sort_order, now(), now(), 1
FROM employee_operations op
CROSS JOIN (VALUES
    ('Consistently exceeded the agreed delivery targets', 1),
    ('Mentored two junior team members through onboarding', 2),
    ('Led a cross-functional process improvement initiative', 3)
) AS h(highlight, sort_order)
WHERE op.operation_type = 'promotion';

INSERT INTO eo_settlement_documents
    (employee_id, pdf_path, gratuity, unused_leave, pro_rata_salary, total, generated_at,
     created_by, created_at, updated_at)
SELECT e.employee_id,
       '/storage/hr/settlements/' || e.employee_number || '.pdf',
       round(s.basic_salary * (EXTRACT(YEAR FROM age(e.termination_date, e.hire_date)) * 0.5), 2),
       round(s.basic_salary / 30 * 12, 2),
       round(s.basic_salary / 30 * 18, 2),
       round(s.basic_salary * (EXTRACT(YEAR FROM age(e.termination_date, e.hire_date)) * 0.5)
             + s.basic_salary / 30 * 12
             + s.basic_salary / 30 * 18, 2),
       e.termination_date::timestamptz + interval '3 days',
       1, now(), now()
FROM employees e
JOIN salary s ON s.employee_id = e.employee_id AND s.is_current
WHERE e.termination_date IS NOT NULL;

INSERT INTO eo_settlement_access_log (settlement_id, accessed_by, action, ip, accessed_at)
SELECT sd.settlement_id, u.uid, u.action,
       '10.30.' || pg_temp.h(sd.settlement_id::text, 250) || '.' || u.uid,
       sd.generated_at + (u.uid || ' hours')::interval
FROM eo_settlement_documents sd
CROSS JOIN (VALUES (11, 'viewed'), (12, 'downloaded'), (13, 'viewed')) AS u(uid, action);

-- =============================================================================
-- 12. Talent: career paths, promotion criteria, cases, nominations, succession
-- =============================================================================

INSERT INTO tal_career_paths
    (from_grade_id, to_grade_id, min_tenure_months, required_competencies, created_at, updated_at, created_by)
SELECT f.job_grade_id, t.job_grade_id,
       CASE WHEN f.grade_level >= 5 THEN 36 ELSE 24 END,
       jsonb_build_array('Ownership', 'Collaboration',
                         CASE WHEN t.grade_level >= 5 THEN 'People Leadership' ELSE 'Analytical Thinking' END),
       now(), now(), 1
FROM job_grades f
JOIN job_grades t ON t.grade_level = f.grade_level + 1;

INSERT INTO tal_promotion_criteria (career_path_id, name, weight, sort_order, created_at, updated_at, created_by)
SELECT cp.path_id, c.name, c.weight, c.sort_order, now(), now(), 1
FROM tal_career_paths cp
CROSS JOIN (VALUES
    ('Minimum tenure in the current grade met',            3, 1),
    ('Last two appraisal ratings at or above target',      5, 2),
    ('Required competencies assessed at level 4 or above', 4, 3),
    ('Mandatory training completed',                       2, 4)
) AS c(name, weight, sort_order);

INSERT INTO tal_employee_criterion_progress
    (employee_id, criterion_id, state, progress_percent, gaps_count, notes,
     updated_by, created_at, updated_at, created_by)
SELECT e.employee_id, pc.criterion_id,
       CASE WHEN pg_temp.h(e.employee_number || pc.criterion_id::text, 10) < 6 THEN 'met' ELSE 'in_progress' END,
       CASE WHEN pg_temp.h(e.employee_number || pc.criterion_id::text, 10) < 6 THEN 100.00
            ELSE round(20 + pg_temp.h(e.employee_number || pc.criterion_id::text || 'p', 60)::numeric, 2) END,
       CASE WHEN pg_temp.h(e.employee_number || pc.criterion_id::text, 10) < 6 THEN 0 ELSE 1 END,
       'Reviewed during the last talent calibration.',
       1, now(), now(), 1
FROM employees e
JOIN job_grades g ON g.job_grade_id = e.job_grade_id
JOIN tal_career_paths cp ON cp.from_grade_id = g.job_grade_id
JOIN tal_promotion_criteria pc ON pc.career_path_id = cp.path_id
WHERE e.is_active AND pg_temp.h(e.employee_number || 'tal', 10) < 4;

INSERT INTO tal_promotion_cases
    (case_no, employee_id, current_grade_id, proposed_grade_id, target_position_id,
     current_salary, proposed_salary, effective_date, justification, status,
     applied_at, created_by, created_at, updated_at)
SELECT 'PC-' || to_char(CURRENT_DATE, 'YYYY') || '-' || lpad(row_number() OVER (ORDER BY e.employee_number)::text, 4, '0'),
       e.employee_id, e.job_grade_id, tg.job_grade_id, e.position_id,
       round(s.basic_salary, 2),
       round(s.basic_salary * 1.15, 2),
       (date_trunc('month', CURRENT_DATE) + interval '1 month')::date,
       'Sustained performance above grade expectations with demonstrated readiness for a broader remit.',
       st.status,
       CASE WHEN st.status = 'applied' THEN now() - interval '10 days' END,
       1, now(), now()
FROM employees e
JOIN salary s ON s.employee_id = e.employee_id AND s.is_current
JOIN job_grades cg ON cg.job_grade_id = e.job_grade_id
JOIN job_grades tg ON tg.grade_level = cg.grade_level + 1
CROSS JOIN LATERAL (
    SELECT (ARRAY['draft','pending_approval','approved','applied','rejected'])
           [1 + pg_temp.h(e.employee_number || 'pcs', 5)] AS status
) st
WHERE e.is_active AND pg_temp.h(e.employee_number || 'case', 8) = 0;

INSERT INTO tal_promotion_nominations
    (employee_id, nominator_user_id, role_label, quote, nominated_at, created_at, updated_at, created_by)
SELECT e.employee_id,
       1 + pg_temp.h(e.employee_number || 'nom', 50),
       (ARRAY['Line Manager','Department Head','HR Business Partner','Peer'])[1 + pg_temp.h(e.employee_number || 'rl', 4)],
       (ARRAY['Raises the bar for the whole team every quarter.',
              'Handles ambiguity better than anyone at this grade.',
              'The first person colleagues go to when something breaks.'])[1 + pg_temp.h(e.employee_number || 'q', 3)],
       (CURRENT_DATE - 45)::date, now(), now(), 1
FROM employees e
WHERE e.is_active AND pg_temp.h(e.employee_number || 'nomsel', 6) = 0;

INSERT INTO tal_succession_candidates
    (position_id, candidate_employee_id, readiness, rank, notes, created_at, updated_at, created_by)
SELECT p.position_id, e.employee_id,
       (ARRAY['ready_now','1_2_years','long_term'])[1 + (rn.rn - 1) % 3],
       rn.rn,
       'Identified in the departmental succession review.',
       now(), now(), 1
FROM positions p
JOIN LATERAL (
    SELECT e2.employee_id, row_number() OVER (ORDER BY e2.employee_number) AS rn
    FROM employees e2
    WHERE e2.dept_id = p.dept_id AND e2.is_active
    ORDER BY e2.employee_number
    LIMIT 3
) rn ON true
JOIN employees e ON e.employee_id = rn.employee_id
WHERE p.position_code LIKE '%-MGR';

-- =============================================================================
-- 13. Service requests
-- =============================================================================

INSERT INTO sr_letter_templates (code, name, body_ar, body_en, header_footer_config, version, created_at, updated_at, created_by)
VALUES
    ('TPL-EMPLOYMENT', 'Employment Letter',
     'نفيد بأن السيد/ة {{employee_name}} يعمل لدينا بوظيفة {{job_title}} منذ {{hire_date}}.',
     'This is to certify that {{employee_name}} has been employed with us as {{job_title}} since {{hire_date}}.',
     '{"header":"logo_top_left","footer":"hr_signature"}'::jsonb, 1, now(), now(), 1),
    ('TPL-SALARY', 'Salary Certificate',
     'نفيد بأن الراتب الشهري الإجمالي للسيد/ة {{employee_name}} هو {{gross_salary}} جنيه مصري.',
     'This is to certify that the gross monthly salary of {{employee_name}} is EGP {{gross_salary}}.',
     '{"header":"logo_top_left","footer":"hr_and_finance_signature"}'::jsonb, 2, now(), now(), 1),
    ('TPL-EXPERIENCE', 'Experience Letter',
     'نفيد بأن السيد/ة {{employee_name}} عمل لدينا في الفترة من {{hire_date}} إلى {{termination_date}}.',
     'This is to certify that {{employee_name}} was employed with us from {{hire_date}} to {{termination_date}}.',
     '{"header":"logo_centered","footer":"hr_signature"}'::jsonb, 1, now(), now(), 1),
    ('TPL-BANK', 'Bank Account Opening Letter',
     'نرجو التكرم بفتح حساب بنكي للسيد/ة {{employee_name}} لصرف الراتب الشهري.',
     'Kindly open a salary account for {{employee_name}} for monthly payroll transfer.',
     '{"header":"logo_top_left","footer":"hr_signature"}'::jsonb, 1, now(), now(), 1);

INSERT INTO sr_request_types
    (code, name_ar, name_en, category, requires_approval, sla_hours, letter_template_id,
     created_at, updated_at, created_by)
SELECT t.code, t.name_ar, t.name_en, t.category, t.requires_approval, t.sla_hours,
       lt.template_id, now(), now(), 1
FROM (VALUES
    ('RT-EMPLOYMENT', 'خطاب إثبات عمل',      'Employment Letter',    'letter', false, 24,  'TPL-EMPLOYMENT'),
    ('RT-SALARY',     'شهادة راتب',          'Salary Certificate',   'letter', true,  48,  'TPL-SALARY'),
    ('RT-EXPERIENCE', 'شهادة خبرة',          'Experience Letter',    'letter', true,  72,  'TPL-EXPERIENCE'),
    ('RT-BANK',       'خطاب فتح حساب بنكي',  'Bank Account Letter',  'letter', false, 24,  'TPL-BANK'),
    ('RT-ITSUP',      'دعم تقني',            'IT Support Ticket',    'ticket', false, 8,   NULL),
    ('RT-FACIL',      'طلب صيانة',           'Facilities Request',   'ticket', false, 24,  NULL),
    ('RT-HRQ',        'استفسار موارد بشرية', 'HR Query',             'ticket', false, 48,  NULL)
) AS t(code, name_ar, name_en, category, requires_approval, sla_hours, template_code)
LEFT JOIN sr_letter_templates lt ON lt.code = t.template_code;

INSERT INTO sr_requests
    (request_no, employee_id, request_type_id, category, payload, status, assigned_to,
     sla_due_at, resolved_at, created_by, created_at, updated_at)
SELECT 'SR-' || to_char(CURRENT_DATE, 'YYYY') || '-' || lpad(row_number() OVER (ORDER BY e.employee_number, rt.code)::text, 5, '0'),
       e.employee_id, rt.type_id, rt.category,
       jsonb_build_object('addressed_to', 'To Whom It May Concern', 'language', 'en', 'copies', 1),
       st.status,
       (SELECT employee_id FROM employees WHERE employee_number = 'EMP-0006'),
       created.at + (rt.sla_hours || ' hours')::interval,
       CASE WHEN st.status IN ('resolved','closed') THEN created.at + interval '20 hours' END,
       1, created.at, now()
FROM employees e
JOIN sr_request_types rt ON pg_temp.h(e.employee_number || rt.code, 10) < 3
CROSS JOIN LATERAL (
    SELECT now() - ((3 + pg_temp.h(e.employee_number || rt.code || 'd', 90)) || ' days')::interval AS at
) created
CROSS JOIN LATERAL (
    SELECT (ARRAY['resolved','resolved','closed','in_progress','submitted','pending_approval','rejected'])
           [1 + pg_temp.h(e.employee_number || rt.code || 's', 7)] AS status
) st
WHERE e.is_active;

INSERT INTO sr_generated_letters
    (request_id, template_id, context_snapshot, pdf_path, reference_no, generated_by,
     generated_at, created_at, updated_at)
SELECT r.request_id, rt.letter_template_id,
       jsonb_build_object(
           'employee_name', e.first_name || ' ' || e.last_name,
           'employee_number', e.employee_number,
           'job_title', p.position_title,
           'hire_date', e.hire_date),
       '/storage/hr/letters/' || r.request_no || '.pdf',
       'LTR-' || substr(r.request_no, 4),
       1,
       r.resolved_at,
       now(), now()
FROM sr_requests r
JOIN sr_request_types rt ON rt.type_id = r.request_type_id
JOIN employees e ON e.employee_id = r.employee_id
LEFT JOIN positions p ON p.position_id = e.position_id
WHERE r.category = 'letter'
  AND r.status IN ('resolved','closed')
  AND rt.letter_template_id IS NOT NULL;

COMMIT;

ANALYZE;

-- =============================================================================
-- Post-seed sanity report
-- =============================================================================
\echo ''
\echo '--- mock_hr seed summary (rows per table) ---'
SELECT c.relname AS table_name, c.reltuples::bigint AS approx_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname;
