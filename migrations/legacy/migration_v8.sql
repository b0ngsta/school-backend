-- ============================================================
-- Migration v8 — run after migration_v7.sql (keeps data).
--   1. users: token_version, must_change_password, password_changed_at
--   2. auth_sessions        (one row per logged-in device; refresh tokens)
--   3. auth_login_attempts  (brute-force throttling + login history)
--   4. audit_logs           (who changed marks, fees, accounts, …)
--   5. indexes for the busiest queries
-- Works on MySQL 8 and TiDB. Run it once:
--   mysql -u root -p < migration_v8.sql
-- ============================================================
USE school_app;

-- 1. users -----------------------------------------------------
ALTER TABLE users
  ADD COLUMN token_version        INT        NOT NULL DEFAULT 0,
  ADD COLUMN must_change_password TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN password_changed_at  DATETIME   NULL;

-- accounts still using a published seed password must change it
UPDATE users SET must_change_password = 1
 WHERE password_hash IN (
   'seedsalt$0ee217e88b95c71bbbb2fa7f2903a08536b0af61f4ee2e707652e7f17d8b8899',
   'seedsalt$33bec7f81eb6495e135b65aa1a04733560a38d6a3a801438ddf78c11439a8a38',
   'seedsalt$5810ea5c2c101bcbb4a68bb2bdc55ed1b11ca37b483fd63ed6ebef095416f93d',
   'seedsalt$63bcf2a32d1dec275804108fc330aa42364c7876a7fb71fe3f609d3e2e1a5fa9',
   'seedsalt$8eb372cb36462e2851c080a901777c09b5d5e80e40771adba7f463c8b38db031'
 );

-- 2. login sessions (times are UTC) ------------------------------
CREATE TABLE IF NOT EXISTS auth_sessions (
  id                  CHAR(32)     NOT NULL PRIMARY KEY,
  user_id             INT          NOT NULL,
  salt                CHAR(64)     NOT NULL,
  generation          INT          NOT NULL DEFAULT 1,
  rotated_at          DATETIME(6)  NULL,
  created_at          DATETIME(6)  NOT NULL,
  last_used_at        DATETIME(6)  NOT NULL,
  expires_at          DATETIME(6)  NOT NULL,
  absolute_expires_at DATETIME(6)  NOT NULL,
  revoked_at          DATETIME(6)  NULL,
  revoke_reason       VARCHAR(30)  NULL,
  ip                  VARCHAR(45)  NULL,
  user_agent          VARCHAR(255) NULL,
  INDEX idx_sessions_user (user_id, last_used_at),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 3. login attempts (times are UTC) ------------------------------
CREATE TABLE IF NOT EXISTS auth_login_attempts (
  id         BIGINT       AUTO_INCREMENT PRIMARY KEY,
  username   VARCHAR(100) NOT NULL,
  ip         VARCHAR(45)  NULL,
  success    TINYINT(1)   NOT NULL,
  created_at DATETIME(6)  NOT NULL,
  INDEX idx_attempts_user_time (username, created_at),
  INDEX idx_attempts_ip_time (ip, created_at),
  INDEX idx_attempts_time (created_at)
) ENGINE=InnoDB;

-- 4. audit log -----------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
  id          BIGINT       AUTO_INCREMENT PRIMARY KEY,
  actor_id    INT          NULL,           -- no FK: keep history after a user is deleted
  action      VARCHAR(60)  NOT NULL,
  entity_type VARCHAR(40)  NULL,
  entity_id   VARCHAR(64)  NULL,
  details     JSON         NULL,
  ip          VARCHAR(45)  NULL,
  request_id  VARCHAR(64)  NULL,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_audit_actor (actor_id, created_at),
  INDEX idx_audit_entity (entity_type, entity_id),
  INDEX idx_audit_time (created_at)
) ENGINE=InnoDB;

-- 5. indexes -------------------------------------------------------
ALTER TABLE users        ADD INDEX idx_users_type_active (user_type, is_active);
ALTER TABLE attendance   ADD INDEX idx_attendance_date (date);
ALTER TABLE fees         ADD INDEX idx_fees_status_due (status, due_date),
                         ADD INDEX idx_fees_student_title (student_id, title);
ALTER TABLE fee_claims   ADD INDEX idx_claims_fee_status (fee_id, status);
ALTER TABLE transactions ADD INDEX idx_transactions_created (created_at);
