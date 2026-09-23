-- ============================================================
-- v2 -> v3 migration: ADDS new feature tables only.
-- Safe to run on an existing v2 database (keeps your data).
-- New installs: just run schema.sql instead.
-- ============================================================
USE school_app;

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

-- Exams
CREATE TABLE IF NOT EXISTS exams (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  name              VARCHAR(150) NOT NULL,
  class_id          INT          NOT NULL,
  start_date        DATE         NOT NULL,
  end_date          DATE         NOT NULL,
  results_published TINYINT(1)   NOT NULL DEFAULT 0,
  created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
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
