# Engineering On-Call Runbook

## 1. Rotation

The on-call rotation runs weekly, handing over at 10:00 on Monday. The outgoing
engineer writes a handover note covering any open incident and any deferred
alert.

## 2. Paging

Severity 1 pages the on-call engineer immediately. Severity 2 is queued to the
next working hour. Anything below that waits for the daily triage.

## 3. Escalation

If an incident is not acknowledged within 15 minutes it escalates to the
engineering manager, and after 30 minutes to the head of engineering.

## 4. Deploy freeze

No deploys after 16:00 on a Friday, and none at all during a Severity 1
incident. The freeze is lifted by the incident commander, not by the deployer.
