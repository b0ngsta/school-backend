-- ============================================================
-- Migration v6 — run after migration_v5.sql (keeps data).
--   1. timetable_slots   (per-teacher weekly timetable, editable by managers)
--   2. holidays          (school holiday calendar, CRUD by managers)
--   3. leave_requests    (staff request leave, managers approve/reject)
--   4. salary_records    (monthly salary statements per staff member)
--   5. staff_attendance  (staff check-in / check-out per day)
-- ============================================================
USE school_app;

-- 1. weekly timetable (day: 0=Mon … 6=Sun) -------------------
CREATE TABLE IF NOT EXISTS timetable_slots (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  teacher_id INT          NOT NULL,
  day        TINYINT      NOT NULL,          -- 0=Mon … 6=Sun
  period     TINYINT      NOT NULL,          -- 1..12
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

-- 2. holidays -------------------------------------------------
CREATE TABLE IF NOT EXISTS holidays (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  date        DATE         NOT NULL UNIQUE,
  name        VARCHAR(150) NOT NULL,
  description VARCHAR(255) NULL,
  created_by  INT          NOT NULL,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 3. leave requests ------------------------------------------
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

-- 4. salary records ------------------------------------------
CREATE TABLE IF NOT EXISTS salary_records (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT           NOT NULL,
  month      TINYINT       NOT NULL,          -- 1..12
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

-- 5. staff attendance (check-in / check-out) ------------------
CREATE TABLE IF NOT EXISTS staff_attendance (
  id       INT  AUTO_INCREMENT PRIMARY KEY,
  user_id  INT  NOT NULL,
  date     DATE NOT NULL,
  in_time  TIME NULL,
  out_time TIME NULL,
  UNIQUE KEY uq_staff_date (user_id, date),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
