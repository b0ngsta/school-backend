# DPDP Act compliance plan

India's **Digital Personal Data Protection Act, 2023** and the **DPDP Rules,
2025** (notified 13 November 2025) apply to this app: it holds personal data
of children, parents and staff. Most duties (notice, consent, children's data,
rights, security, breach reporting) apply **from May 2027**, 18 months after
the Rules were notified. Penalties go up to ₹250 crore for weak security and
₹200 crore for children's-data or breach-reporting failures.

> This is an engineering plan, not legal advice. Have a lawyer review the
> privacy notice, the consent wording and the retention schedule.

## Who is responsible

- The **school** decides why and how data is used, so it is the **Data
  Fiduciary**. If you run this app for schools, you are their **Data
  Processor**; sign a data-processing agreement with each school. If you run
  it as your own platform with your own purposes, you may be a fiduciary too.
- Every vendor that touches the data is a processor of the school: TiDB
  Cloud, the hosting provider, the email provider, the SMS provider, the
  payment gateway, Sentry. Each needs a contract with security and deletion
  clauses (Rule 6).

## What the law asks, mapped to this app

| Duty | Section / Rule | What it means here |
|---|---|---|
| Notice | S.5, R.3 | Plain-language notice listing each data item and purpose, how to withdraw consent, exercise rights and complain to the Board. English plus the school's local language. |
| Consent | S.6 | Specific, per purpose, as easy to withdraw as to give, recorded. |
| **Children** (under 18) | S.9, R.10, R.12, Sch. 4 | **Verifiable consent of a parent** before processing a child's data, and no tracking, behavioural monitoring or targeted advertising. Schools are exempt from the consent and tracking rules **only** for tracking and behavioural monitoring for educational activities or the children's safety (attendance, marks, bus GPS). Everything else (e.g. publishing photos) still needs parental consent. |
| Parent is an adult | R.10 | The school may rely on reliable identity and age details it already holds (admission records), or details the parent provides or brings via DigiLocker. |
| Accuracy | S.8(3) | Data used for decisions (marks, fees) must be complete and correct. |
| Security | S.8(5), R.6 | Encryption or masking, access control, logs and monitoring of access, backups, logs kept **1 year**, processor contracts. |
| Breach | S.8(6), R.7 | Tell affected people **without delay**; tell the Data Protection Board without delay, with a detailed report **within 72 hours**. |
| Erasure | S.8(7), R.8 | Delete data once its purpose is served, unless a law requires keeping it. |
| Rights | S.11–14, R.14 | Access (summary of data and who it was shared with), correction, erasure, grievance, nominee. Answer within **90 days**; publish how to ask. |
| Grievance contact | S.8(9)–(10) | Publish a contact who answers privacy questions, and a way to resolve complaints. |

## Already in place in this backend

- TLS to the database; passwords hashed with Argon2; brute-force throttling.
- Role- and section-based access: teachers only see their sections.
- Audit log of sensitive changes (accounts, marks, fees, attendance,
  salaries, imports and **exports** of user lists).
- Uploaded files are private, served through signed links that expire, and
  photos have GPS/EXIF removed.
- Error tracking (Sentry) sends no request bodies and no personal data.
- CSV import files are processed in memory and never stored.
- Every user can now have an email, which the rights and breach notices below need.

## What to build

### Backend

1. **Privacy notices**: a `privacy_notices` table (version, language, text,
   published_at) and `GET /privacy/notice`.
2. **Consent records**: a `consents` table with student, parent account,
   notice version, purposes (e.g. `core_education`, `photos_in_publications`,
   `sms_updates`), verification method (`school_records` / `digilocker`),
   given_at, withdrawn_at and IP. Endpoints:
   - `POST /privacy/consent` and `POST /privacy/consent/withdraw` (parent app);
   - `GET /students/{id}/consent` (staff view).
3. **Gate optional processing on consent**: e.g. hide a student's photo
   outside the school's own staff screens, and skip optional SMS when consent
   is missing or withdrawn.
4. **Right to access**: `GET /student/my-data` (and a staff equivalent)
   returns everything held about the person, plus the list of processors.
5. **Correction requests**: the parent submits a change and a manager
   approves it. Approved changes go through the existing edit endpoints, so
   they are audited.
6. **Erasure and retention**:
   - a `left_on` / `status` field for students and staff;
   - a nightly job that anonymises or deletes data past its retention period
     (photos included);
   - an admin "erase on request" action with an audit entry.

   Keep what education rules require (admission register, transfer
   certificates, results) and erase the rest.
7. **Grievances**: `POST /privacy/grievances` plus a staff inbox with the date
   due (90 days).
8. **Nominee**: an optional nominee name and contact per account.
9. **Logs for one year**: raise `auth_login_attempts` retention from 30 to 365
   days, keep the JSON access log for a year in log storage, and add
   audit entries for *views* of a student's full profile and of report cards
   (who looked at whose data).
10. **Breach notices**: a CLI command that emails a prepared notice to a list
    of affected users (uses the new email layer), plus a 72-hour report
    template for the Board in `docs/`.
11. **Data location**: hosting the database in India (Mumbai/Hyderabad)
    is not required by the Act today, but it avoids future transfer rules and
    is faster for Indian users. See OPERATIONS.md, "Region".

### Frontend (parent and staff apps)

- A first-launch privacy notice, shown again when the version changes, with a
  checkbox per optional purpose.
- *Settings → Privacy*: see and withdraw consents, download my data, request a
  correction, request erasure, contact the grievance officer, add a nominee.
- No advertising or behavioural-analytics SDKs in the parent app (children's
  data). Crash reporting without personal data is fine.
- Publishing children's photos (website, social media) only with recorded consent.

### School processes (no code)

- Name a grievance officer and publish the contact in the app and on the website.
- Keep a data inventory: each field, why it is held, where it is stored, who
  can see it, and how long it is kept.
- Agree a retention schedule with the school's legal adviser.
- Sign processor agreements with every vendor.
- Write a breach runbook (who decides, who emails, the Board report) and test it once a year.
- Train staff: shared logins, exporting lists to personal phones and posting
  photos in WhatsApp groups are the common leaks.

## Suggested timeline

| By | Done |
|---|---|
| Dec 2026 | Data inventory, retention schedule, grievance contact, 1-year log retention |
| Feb 2027 | Notice + consent records + parent app screens |
| Mar 2027 | My-data export, correction requests, grievance inbox |
| Apr 2027 | Erasure job, breach runbook rehearsed, vendor agreements signed |
| May 2027 | Obligations in force |

## Sources

- DPDP Rules 2025 text (rules 6, 7, 8, 10, 12, 14 and Schedule 4): https://dpdpa.com/dpdparules/rule6.html
- Phased commencement: https://www.azbpartners.com/bank/indias-digital-personal-data-protection-act-phased-rollout-and-key-compliance-milestones/
- Schools and children's data: https://ssrana.in/articles/schools-coaching-centres-platforms-processing-childrens-data-are-you-compliant/
