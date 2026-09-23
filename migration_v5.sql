-- ============================================================
-- Migration v5 — run after migration_v4.sql (keeps data).
--   1. New roles: coordinator, driver
--   2. class_subjects  (subjects entered at class creation)
--   3. exam_papers     (subject-wise question papers per exam+class)
--   4. fee_claims      (parent-submitted payment proofs, staff approval)
--   5. exams.class_id nullable (NULL = exam applies to ALL classes)
-- ============================================================
USE school_app;

-- 1. roles ----------------------------------------------------
ALTER TABLE users MODIFY user_type
  ENUM('admin','principal','sub_admin','coordinator','driver','teacher','student') NOT NULL;

-- seed logins: coordinator / coord123 · driver / driver123
INSERT INTO users (username, password_hash, full_name, user_type) VALUES
('coordinator', 'seedsalt$63bcf2a32d1dec275804108fc330aa42364c7876a7fb71fe3f609d3e2e1a5fa9', 'Coordinator', 'coordinator'),
('driver',      'seedsalt$8eb372cb36462e2851c080a901777c09b5d5e80e40771adba7f463c8b38db031', 'Driver',      'driver')
ON DUPLICATE KEY UPDATE username = username;

-- 2. subjects per class --------------------------------------
CREATE TABLE IF NOT EXISTS class_subjects (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  class_id   INT          NOT NULL,
  name       VARCHAR(100) NOT NULL,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_class_subject (class_id, name),
  FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 3. subject-wise exam papers --------------------------------
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

-- 4. fee payment claims (proof screenshots) ------------------
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

-- 5. exams can target all classes ----------------------------
ALTER TABLE exams DROP FOREIGN KEY exams_ibfk_1;
ALTER TABLE exams MODIFY class_id INT NULL;
ALTER TABLE exams ADD CONSTRAINT fk_exams_class
  FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE;
