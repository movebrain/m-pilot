-- MySQL 스키마(spec 5.2). 문자셋 utf8mb4는 docker-compose의 DB 설정에 둔다. 소유: MySQL.
-- case_id 채번(SC-YYYYMMDD-NNN)은 동시 접수에도 겹치지 않게 DB 안에서 처리한다(AC-04.1).

CREATE TABLE IF NOT EXISTS cases (
  case_id         VARCHAR(32)  PRIMARY KEY,
  company_id      VARCHAR(32)  NOT NULL DEFAULT 'COMP-DEMO',
  memo            TEXT         NULL,
  photo_path      VARCHAR(255) NULL,
  status          VARCHAR(8)   NOT NULL,
  branch          VARCHAR(8)   NOT NULL,
  summary         TEXT         NULL,
  proposal        JSON         NULL,
  cause_found     TEXT         NULL,
  engineer_action TEXT         NULL,
  resolution      VARCHAR(8)   NULL,
  resolution_note TEXT         NULL,
  actor           VARCHAR(64)  NULL,
  diverged        TINYINT(1)   NULL,
  source          VARCHAR(16)  NOT NULL,
  confirmed       TINYINT(1)   NOT NULL DEFAULT 0,
  created_at      DATETIME     NOT NULL,
  updated_at      DATETIME     NOT NULL
);

CREATE TABLE IF NOT EXISTS case_errors (
  case_id   VARCHAR(32) NOT NULL,
  seq       TINYINT     NOT NULL,
  equipment VARCHAR(16) NOT NULL,
  code      VARCHAR(8)  NOT NULL,
  PRIMARY KEY (case_id, equipment, code),
  FOREIGN KEY (case_id) REFERENCES cases (case_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS case_messages (
  case_id    VARCHAR(32) NOT NULL,
  role       VARCHAR(16) NOT NULL,
  content    TEXT        NOT NULL,
  created_at DATETIME    NOT NULL,
  FOREIGN KEY (case_id) REFERENCES cases (case_id) ON DELETE CASCADE
);
