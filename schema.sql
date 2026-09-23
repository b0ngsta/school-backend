-- ============================================================
-- School Management App — MySQL schema  (v7)
-- (upgrading with data? run migration_v3.sql → v4 → v5 → v6 → migration_v7.sql instead)
-- WARNING: drops & recreates all tables (dev stage).
-- Paste the whole file into MySQL.
-- ============================================================

CREATE DATABASE IF NOT EXISTS school_app
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE school_app;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS lesson_files, lesson_plans, homeworks, student_remarks,
  report_cards, notices, teacher_assignments, student_profiles,
  teacher_profiles, enquiries, transactions, fee_claims, fees, attendance,
  exam_results, exam_papers, exams, vehicles, sms_messages, class_subjects,
  timetable_slots, holidays, leave_requests, salary_records, staff_attendance,
  calendar_event_dates, calendar_events, sections, classes, users;
SET FOREIGN_KEY_CHECKS = 1;

-- ---------- users ----------
CREATE TABLE users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(50)  NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  full_name     VARCHAR(100) NOT NULL,
  email         VARCHAR(120) NULL,
  phone         VARCHAR(20)  NULL,
  photo_path    VARCHAR(255) NULL,
  user_type     ENUM('admin','principal','sub_admin','coordinator','driver','teacher','student') NOT NULL,
  is_active     TINYINT(1)   NOT NULL DEFAULT 1,
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------- classes & sections ----------
CREATE TABLE classes (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(50) NOT NULL UNIQUE,
  fee_amount DECIMAL(10,2) NULL,          -- standard fee for this class
  created_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE sections (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  class_id   INT         NOT NULL,
  name       VARCHAR(10) NOT NULL,
  created_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_class_section (class_id, name),
  FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- subjects taught in a class (entered at class creation)
CREATE TABLE class_subjects (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  class_id   INT          NOT NULL,
  name       VARCHAR(100) NOT NULL,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_class_subject (class_id, name),
  FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- teacher extra info ----------
CREATE TABLE teacher_profiles (
  user_id       INT PRIMARY KEY,
  subject       VARCHAR(100) NULL,
  qualification VARCHAR(200) NULL,
  joining_date  DATE         NULL,
  address       VARCHAR(255) NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- teacher <-> class/section assignments ----------
-- role: class_teacher (max 1 per teacher; 1 per section) or subject_teacher (many)
CREATE TABLE teacher_assignments (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  teacher_id INT NOT NULL,
  class_id   INT NOT NULL,
  section_id INT NOT NULL,
  role       ENUM('class_teacher','subject_teacher') NOT NULL,
  subject    VARCHAR(100) NULL,              -- for subject_teacher
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_assignment (teacher_id, section_id, role, subject),
  FOREIGN KEY (teacher_id) REFERENCES users(id)    ON DELETE CASCADE,
  FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE CASCADE,
  FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- student info ----------
CREATE TABLE student_profiles (
  user_id        INT PRIMARY KEY,
  class_id       INT          NULL,
  section_id     INT          NULL,
  roll_no        VARCHAR(20)  NULL,
  admission_no   VARCHAR(30)  NULL,
  dob            DATE         NULL,
  father_name    VARCHAR(100) NULL,
  mother_name    VARCHAR(100) NULL,
  guardian_phone VARCHAR(20)  NULL,
  address        VARCHAR(255) NULL,
  FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
  FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE SET NULL,
  FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- ---------- homework status (per student, shown to parents) ----------
CREATE TABLE homeworks (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT          NOT NULL,
  title      VARCHAR(200) NOT NULL,
  due_date   DATE         NULL,
  status     ENUM('pending','submitted','late') NOT NULL DEFAULT 'pending',
  note       TEXT         NULL,
  created_by INT          NOT NULL,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- remarks from school to parents ----------
CREATE TABLE student_remarks (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT       NOT NULL,
  remark     TEXT      NOT NULL,
  created_by INT       NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- report cards ----------
CREATE TABLE report_cards (
  id         INT          AUTO_INCREMENT PRIMARY KEY,
  student_id INT          NOT NULL,
  created_by INT          NOT NULL,
  term       VARCHAR(50)  NOT NULL,
  grades     JSON         NULL,
  remarks    TEXT         NULL,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_student_term (student_id, term),
  FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- lesson plans ----------
CREATE TABLE lesson_plans (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  teacher_id     INT          NOT NULL,
  class_id       INT          NOT NULL,
  section_id     INT          NULL,
  heading        VARCHAR(200) NOT NULL,
  duration_start DATE         NULL,
  duration_end   DATE         NULL,
  final_remark   TEXT         NULL,
  created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (teacher_id) REFERENCES users(id)    ON DELETE CASCADE,
  FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE CASCADE,
  FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE lesson_files (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  lesson_plan_id INT          NOT NULL,
  file_path      VARCHAR(255) NOT NULL,
  original_name  VARCHAR(255) NOT NULL,
  content_type   VARCHAR(100) NULL,
  uploaded_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (lesson_plan_id) REFERENCES lesson_plans(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- notices (admin/principal -> staff) ----------
CREATE TABLE notices (
  id         INT          AUTO_INCREMENT PRIMARY KEY,
  created_by INT          NOT NULL,
  title      VARCHAR(200) NOT NULL,
  body       TEXT         NOT NULL,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- seeds (password format: salt$sha256(salt:password)) ----------
-- admin / admin123 · principal / principal123 · subadmin / sub123
-- coordinator / coord123 · driver / driver123
INSERT INTO users (username, password_hash, full_name, user_type) VALUES
('admin',       'seedsalt$0ee217e88b95c71bbbb2fa7f2903a08536b0af61f4ee2e707652e7f17d8b8899', 'Admin',        'admin'),
('principal',   'seedsalt$33bec7f81eb6495e135b65aa1a04733560a38d6a3a801438ddf78c11439a8a38', 'Principal',    'principal'),
('subadmin',    'seedsalt$5810ea5c2c101bcbb4a68bb2bdc55ed1b11ca37b483fd63ed6ebef095416f93d', 'Sub Admin',    'sub_admin'),
('coordinator', 'seedsalt$63bcf2a32d1dec275804108fc330aa42364c7876a7fb71fe3f609d3e2e1a5fa9', 'Coordinator',  'coordinator'),
('driver',      'seedsalt$8eb372cb36462e2851c080a901777c09b5d5e80e40771adba7f463c8b38db031', 'Driver',       'driver');

-- ============================================================
-- v3 feature tables (same definitions as migration_v3.sql)
-- ============================================================
-- Reception: admission enquiries
CREATE TABLE IF NOT EXISTS enquiries (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  parent_name      VARCHAR(100) NOT NULL,
  student_name     VARCHAR(100) NOT NULL,
  class_interested VARCHAR(50)  NULL,
  contact          VARCHAR(20)  NOT NULL,
  email            VARCHAR(120) NULL,
  notes            TEXT         NULL,
  status           ENUM('new','follow_up','converted','closed') NOT NULL DEFAULT 'new',
  created_by       INT          NOT NULL,
  created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Fees
CREATE TABLE IF NOT EXISTS fees (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT           NOT NULL,
  title      VARCHAR(100)  NOT NULL,          -- e.g. 'Term 1 Fees 2026'
  amount     DECIMAL(10,2) NOT NULL,
  due_date   DATE          NULL,
  status     ENUM('pending','paid') NOT NULL DEFAULT 'pending',
  paid_date  DATE          NULL,
  method     VARCHAR(30)   NULL,
  created_at TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Payment transactions
CREATE TABLE IF NOT EXISTS transactions (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  reference  VARCHAR(30)   NOT NULL UNIQUE,
  student_id INT           NULL,
  fee_id     INT           NULL,
  amount     DECIMAL(10,2) NOT NULL,
  method     ENUM('cash','card','upi','netbanking','wallet') NOT NULL,
  status     ENUM('success','pending','failed') NOT NULL DEFAULT 'success',
  created_at TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (fee_id)     REFERENCES fees(id)  ON DELETE SET NULL
) ENGINE=InnoDB;

-- Attendance (one row per student per day)
CREATE TABLE IF NOT EXISTS attendance (
  id         INT  AUTO_INCREMENT PRIMARY KEY,
  student_id INT  NOT NULL,
  section_id INT  NOT NULL,
  date       DATE NOT NULL,
  status     ENUM('present','absent','leave') NOT NULL,
  marked_by  INT  NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_student_date (student_id, date),
  FOREIGN KEY (student_id) REFERENCES users(id)    ON DELETE CASCADE,
  FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
  FOREIGN KEY (marked_by)  REFERENCES users(id)    ON DELETE CASCADE
) ENGINE=InnoDB;

-- Exams (class_id NULL = exam applies to ALL classes)
CREATE TABLE IF NOT EXISTS exams (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  name              VARCHAR(150) NOT NULL,
  class_id          INT          NULL,
  start_date        DATE         NOT NULL,
  end_date          DATE         NOT NULL,
  results_published TINYINT(1)   NOT NULL DEFAULT 0,
  paper_path        VARCHAR(255) NULL,
  paper_name        VARCHAR(255) NULL,
  created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Exam results (per student per exam)
CREATE TABLE IF NOT EXISTS exam_results (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  exam_id    INT NOT NULL,
  student_id INT NOT NULL,
  marks      DECIMAL(6,2)  NULL,
  grade      VARCHAR(10)   NULL,
  remarks    VARCHAR(255)  NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_exam_student (exam_id, student_id),
  FOREIGN KEY (exam_id)    REFERENCES exams(id) ON DELETE CASCADE,
  FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Subject-wise exam papers (per exam + class + subject)
CREATE TABLE IF NOT EXISTS exam_papers (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  exam_id     INT          NOT NULL,
  class_id    INT          NOT NULL,
  subject     VARCHAR(100) NOT NULL,
  paper_path  VARCHAR(255) NOT NULL,
  paper_name  VARCHAR(255) NOT NULL,
  uploaded_by INT          NOT NULL,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_exam_class_subject (exam_id, class_id, subject),
  FOREIGN KEY (exam_id)     REFERENCES exams(id)   ON DELETE CASCADE,
  FOREIGN KEY (class_id)    REFERENCES classes(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES users(id)   ON DELETE CASCADE
) ENGINE=InnoDB;

-- Fee payment claims: parent uploads proof, staff approves/rejects
CREATE TABLE IF NOT EXISTS fee_claims (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  fee_id          INT           NOT NULL,
  student_id      INT           NOT NULL,
  amount          DECIMAL(10,2) NULL,
  method          ENUM('cash','card','upi','netbanking','wallet') NOT NULL DEFAULT 'upi',
  reference_no    VARCHAR(100)  NULL,
  note            VARCHAR(255)  NULL,
  screenshot_path VARCHAR(255)  NOT NULL,
  screenshot_name VARCHAR(255)  NULL,
  status          ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
  reviewed_by     INT           NULL,
  reviewed_at     TIMESTAMP     NULL,
  review_note     VARCHAR(255)  NULL,
  created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (fee_id)      REFERENCES fees(id)  ON DELETE CASCADE,
  FOREIGN KEY (student_id)  REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Weekly timetable (day: 0=Mon … 6=Sun)
CREATE TABLE IF NOT EXISTS timetable_slots (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  teacher_id INT          NOT NULL,
  day        TINYINT      NOT NULL,
  period     TINYINT      NOT NULL,
  class_id   INT          NOT NULL,
  section_id INT          NOT NULL,
  subject    VARCHAR(100) NULL,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_teacher_day_period (teacher_id, day, period),
  FOREIGN KEY (teacher_id) REFERENCES users(id)    ON DELETE CASCADE,
  FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE CASCADE,
  FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- School calendar events (named events: holiday / exam / event)
CREATE TABLE IF NOT EXISTS calendar_events (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  title      VARCHAR(150) NOT NULL UNIQUE,
  category   ENUM('holiday','exam','event') NOT NULL DEFAULT 'event',
  created_by INT       NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Dates marked for each calendar event
CREATE TABLE IF NOT EXISTS calendar_event_dates (
  id       INT  AUTO_INCREMENT PRIMARY KEY,
  event_id INT  NOT NULL,
  date     DATE NOT NULL,
  UNIQUE KEY uq_event_date (event_id, date),
  FOREIGN KEY (event_id) REFERENCES calendar_events(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- default events shown in the calendar "Add event" popup
INSERT IGNORE INTO calendar_events (title, category) VALUES
  ('Holiday', 'holiday'),
  ('Exam', 'exam'),
  ('PTM', 'event'),
  ('Annual Function Day', 'event');

-- School holidays (event_id set when auto-synced from a holiday-category event)
CREATE TABLE IF NOT EXISTS holidays (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  date        DATE         NOT NULL UNIQUE,
  name        VARCHAR(150) NOT NULL,
  description VARCHAR(255) NULL,
  event_id    INT          NULL,
  created_by  INT          NOT NULL,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (event_id)   REFERENCES calendar_events(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Staff leave requests
CREATE TABLE IF NOT EXISTS leave_requests (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  user_id     INT          NOT NULL,
  from_date   DATE         NOT NULL,
  to_date     DATE         NOT NULL,
  reason      VARCHAR(255) NOT NULL,
  status      ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
  reviewed_by INT          NULL,
  reviewed_at TIMESTAMP    NULL,
  review_note VARCHAR(255) NULL,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id)     REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Monthly salary statements
CREATE TABLE IF NOT EXISTS salary_records (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT           NOT NULL,
  month      TINYINT       NOT NULL,
  year       SMALLINT      NOT NULL,
  amount     DECIMAL(10,2) NOT NULL,
  status     ENUM('paid','pending') NOT NULL DEFAULT 'paid',
  note       VARCHAR(255)  NULL,
  created_by INT           NOT NULL,
  created_at TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_user_month (user_id, year, month),
  FOREIGN KEY (user_id)    REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Staff daily check-in / check-out
CREATE TABLE IF NOT EXISTS staff_attendance (
  id       INT  AUTO_INCREMENT PRIMARY KEY,
  user_id  INT  NOT NULL,
  date     DATE NOT NULL,
  in_time  TIME NULL,
  out_time TIME NULL,
  UNIQUE KEY uq_staff_date (user_id, date),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Transport vehicles
CREATE TABLE IF NOT EXISTS vehicles (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  vehicle_no   VARCHAR(30)  NOT NULL UNIQUE,
  driver_name  VARCHAR(100) NOT NULL,
  driver_phone VARCHAR(20)  NULL,
  route_name   VARCHAR(150) NULL,
  capacity     INT          NULL,
  status       ENUM('active','maintenance') NOT NULL DEFAULT 'active',
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Bulk SMS history (gateway integration is stubbed for now)
CREATE TABLE IF NOT EXISTS sms_messages (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  recipient_group  VARCHAR(100) NOT NULL,   -- 'All Parents' | 'All Teachers' | 'Class 5 — A'
  template         VARCHAR(100) NULL,
  message          TEXT         NOT NULL,
  recipients_count INT          NOT NULL DEFAULT 0,
  status           ENUM('sent','failed') NOT NULL DEFAULT 'sent',
  created_by       INT          NOT NULL,
  created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
