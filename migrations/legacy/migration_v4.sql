-- ============================================================
-- v3 -> v4 migration (additive, keeps your data)
-- profile photos · class fee amount · exam question papers · exam results
-- ============================================================
USE school_app;

ALTER TABLE users   ADD COLUMN photo_path VARCHAR(255) NULL AFTER phone;
ALTER TABLE classes ADD COLUMN fee_amount DECIMAL(10,2) NULL AFTER name;
ALTER TABLE exams
  ADD COLUMN paper_path VARCHAR(255) NULL AFTER results_published,
  ADD COLUMN paper_name VARCHAR(255) NULL AFTER paper_path;

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
