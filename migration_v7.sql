-- ============================================================
-- Migration v7 — run after migration_v6.sql (keeps data).
--   1. calendar_events       (named school events: holiday / exam / event)
--   2. calendar_event_dates  (dates marked for each event)
--   3. holidays.event_id     (links auto-synced holiday rows to their event)
-- Holiday-category events are mirrored into `holidays` so the existing
-- mobile Holidays tab keeps working unchanged.
-- ============================================================
USE school_app;

-- 1. calendar events -----------------------------------------
CREATE TABLE IF NOT EXISTS calendar_events (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  title      VARCHAR(150) NOT NULL UNIQUE,
  category   ENUM('holiday','exam','event') NOT NULL DEFAULT 'event',
  created_by INT       NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- 2. dates marked per event ----------------------------------
CREATE TABLE IF NOT EXISTS calendar_event_dates (
  id       INT  AUTO_INCREMENT PRIMARY KEY,
  event_id INT  NOT NULL,
  date     DATE NOT NULL,
  UNIQUE KEY uq_event_date (event_id, date),
  FOREIGN KEY (event_id) REFERENCES calendar_events(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 3. link synced holiday rows back to their calendar event ---
ALTER TABLE holidays
  ADD COLUMN event_id INT NULL,
  ADD CONSTRAINT fk_holidays_event
    FOREIGN KEY (event_id) REFERENCES calendar_events(id) ON DELETE CASCADE;

-- default events shown in the "Add event" popup --------------
INSERT IGNORE INTO calendar_events (title, category) VALUES
  ('Holiday', 'holiday'),
  ('Exam', 'exam'),
  ('PTM', 'event'),
  ('Annual Function Day', 'event');
