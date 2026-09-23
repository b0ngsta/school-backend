-- ============================================================
-- School Surveillance App :) — MySQL schema
-- Paste this whole file into MySQL (mysql CLI / Workbench)
-- ============================================================

CREATE DATABASE IF NOT EXISTS school_app
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE school_app;

-- ---------- users ----------
CREATE TABLE IF NOT EXISTS users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(50)  NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  full_name     VARCHAR(100) NOT NULL,
  email         VARCHAR(120) NULL,
  phone         VARCHAR(20)  NULL,
  user_type     ENUM('admin','sub_admin','teacher','student') NOT NULL,
  is_active     TINYINT(1)   NOT NULL DEFAULT 1,
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------- classes & sections ----------
CREATE TABLE IF NOT EXISTS classes (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(50) NOT NULL UNIQUE,          -- e.g. 'Class 5'
  created_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sections (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  class_id   INT         NOT NULL,
  name       VARCHAR(10) NOT NULL,                 -- e.g. 'A'
  created_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_class_section (class_id, name),
  FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- teacher info (maintained by sub_admin) ----------
CREATE TABLE IF NOT EXISTS teacher_profiles (
  user_id       INT PRIMARY KEY,
  subject       VARCHAR(100) NULL,
  qualification VARCHAR(200) NULL,
  joining_date  DATE         NULL,
  address       VARCHAR(255) NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- student info ----------
CREATE TABLE IF NOT EXISTS student_profiles (
  user_id       INT PRIMARY KEY,
  class_id      INT          NULL,
  section_id    INT          NULL,
  roll_no       VARCHAR(20)  NULL,
  guardian_name VARCHAR(100) NULL,
  guardian_phone VARCHAR(20) NULL,
  FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
  FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE SET NULL,
  FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- ---------- lesson plans (uploaded by teachers) ----------
CREATE TABLE IF NOT EXISTS lesson_plans (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  teacher_id  INT          NOT NULL,
  class_id    INT          NOT NULL,
  section_id  INT          NULL,
  subject     VARCHAR(100) NOT NULL,
  title       VARCHAR(200) NOT NULL,
  description TEXT         NULL,
  lesson_date DATE         NULL,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (teacher_id) REFERENCES users(id)    ON DELETE CASCADE,
  FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE CASCADE,
  FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- attached photos / PDFs for a lesson plan
CREATE TABLE IF NOT EXISTS lesson_files (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  lesson_plan_id INT          NOT NULL,
  file_path      VARCHAR(255) NOT NULL,
  original_name  VARCHAR(255) NOT NULL,
  content_type   VARCHAR(100) NULL,
  uploaded_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (lesson_plan_id) REFERENCES lesson_plans(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- report cards ----------
CREATE TABLE IF NOT EXISTS report_cards (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT          NOT NULL,
  teacher_id INT          NOT NULL,
  term       VARCHAR(50)  NOT NULL,                -- e.g. 'Term 1 2026'
  grades     JSON         NULL,                    -- {"Math": 92, "Science": 88}
  remarks    TEXT         NULL,
  file_path  VARCHAR(255) NULL,                    -- optional uploaded PDF
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_student_term (student_id, term),
  FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- notices (admin -> all teachers) ----------
CREATE TABLE IF NOT EXISTS notices (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  created_by INT          NOT NULL,
  title      VARCHAR(200) NOT NULL,
  body       TEXT         NOT NULL,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- seed: default admin ----------
-- username: admin   password: admin123
-- (format: salt$sha256(salt:password) — matches app/security.py)
INSERT INTO users (username, password_hash, full_name, user_type)
VALUES ('admin',
        'seedsalt$0ee217e88b95c71bbbb2fa7f2903a08536b0af61f4ee2e707652e7f17d8b8899',
        'Principal Admin',
        'admin')
ON DUPLICATE KEY UPDATE username = username;
