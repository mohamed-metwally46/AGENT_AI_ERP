-- Bootstrap for the mock_hr database.
-- Executed once by the postgres docker entrypoint on an empty data volume.
-- The postgres entrypoint does not recurse into subdirectories, so this single
-- file pulls in the schema + seed tree mounted read-only at /database.

\set ON_ERROR_STOP on

\echo '=== mock_hr :: applying HR schema ==='
\i /database/schema/hr/01_foundation.sql
\i /database/schema/hr/02_employee.sql
\i /database/schema/hr/03_salary_payroll.sql
\i /database/schema/hr/04_time_attendance.sql
\i /database/schema/hr/05_leave.sql
\i /database/schema/hr/06_performance.sql
\i /database/schema/hr/07_training.sql
\i /database/schema/hr/08_recruitment_talent.sql
\i /database/schema/hr/09_service_requests.sql

\echo '=== mock_hr :: seeding fake HR data ==='
\i /database/seed/hr_seed.sql

-- Last, so the grants cover every table the schema files created.
\echo '=== mock_hr :: creating the read-only agent role ==='
\i /database/schema/hr/10_readonly_role.sql

\echo '=== mock_hr :: ready ==='
