-- M-Pilot ERD v2 (MySQL 8.0) — ERD Cloud 가져오기용 초안. 모든 데이터는 가상이다.
-- 가져오기: ERD Cloud 새 ERD → 가져오기(Import) → SQL(DDL) → 이 파일 내용 붙여 넣기.
-- 시각은 모두 UTC로 저장한다. 키 컬럼(equipment, code, case_id)은 구현 때 utf8mb4_0900_bin 콜레이션을 쓴다.
-- 변경점(v2): service_cases.memo NOT NULL, 시각 DATETIME, unit_no·location·checked_values 추가,
--            error_codes.required_safety_rules NULL 허용.

CREATE TABLE error_codes (
  equipment                  VARCHAR(32)  NOT NULL COMMENT '설비 (Mini Loader·SRM·AGV·RTV)',
  code                       VARCHAR(32)  NOT NULL COMMENT '에러코드. 설비 안에서 유일',
  name                       VARCHAR(255) NOT NULL COMMENT '에러명',
  severity                   VARCHAR(8)   NOT NULL COMMENT '심각도 (상·중·하)',
  action_owner               VARCHAR(16)  NOT NULL COMMENT '조치 구분 (고객사 조치·엔지니어 필요)',
  cause                      TEXT         NOT NULL COMMENT '코드 표의 원인',
  action                     TEXT         NOT NULL COMMENT '코드 표의 조치',
  manual_id                  VARCHAR(64)  NOT NULL COMMENT '출처 PDF 식별자 (매뉴얼 테이블은 없음)',
  page                       INT          NOT NULL COMMENT '출처 쪽 (1 이상)',
  section                    VARCHAR(255) NOT NULL COMMENT '출처 절',
  customer_procedure_ref_ids JSON         NOT NULL COMMENT '고객 수행이 허용된 매뉴얼 청크 ID 배열. 사람이 작성',
  required_safety_rules      JSON         NULL     COMMENT '[v2] 고객 안내에 반드시 들어갈 안전 문구 배열. 사람이 작성. 고객 안내를 만들지 않는 코드(support 전용)는 NULL',
  PRIMARY KEY (equipment, code)
) COMMENT='에러코드 사전';

CREATE TABLE service_cases (
  case_id         VARCHAR(64)  NOT NULL COMMENT '접수번호. 신규 SC-<UUID 앞 8자리>, 시드는 원본 ID',
  equipment       VARCHAR(32)  NOT NULL COMMENT '설비 (FK)',
  code            VARCHAR(32)  NOT NULL COMMENT '에러코드 (FK). 유사 코드로 대체하지 않음',
  memo            VARCHAR(2000) NOT NULL COMMENT '증상 메모 (필수, 1~2,000자)',
  source          VARCHAR(16)  NOT NULL COMMENT 'seed(시드) / reported(앱 신고)',
  status          VARCHAR(16)  NOT NULL COMMENT 'received / in_progress / completed',
  route           VARCHAR(16)  NOT NULL COMMENT 'self_guide / support',
  guide_failure   VARCHAR(32)  NULL     COMMENT '셀프 가이드 후보였다가 엔지니어로 넘어온 사유 (held / generation_failed)',
  guidance_answer JSON         NULL     COMMENT '셀프 가이드 내용·단계·출처. self_guide에서만',
  created_at      DATETIME     NOT NULL COMMENT '신고 시각 (UTC)',
  completed_at    DATETIME     NULL     COMMENT '완료 시각 (UTC). 완료 건에만. 셀프 가이드는 접수 시각',
  unit_no         VARCHAR(32)  NULL     COMMENT '[v2] 호기 (예: SRM-04)',
  location        VARCHAR(255) NULL     COMMENT '[v2] 발생 위치 (셀 주소·스테이션)',
  PRIMARY KEY (case_id),
  CONSTRAINT fk_service_cases_error_codes
    FOREIGN KEY (equipment, code) REFERENCES error_codes (equipment, code)
) COMMENT='신고 접수 대장';

CREATE TABLE resolutions (
  case_id        VARCHAR(64) NOT NULL COMMENT '접수번호 (PK이자 FK). 건당 한 행',
  cause          TEXT        NOT NULL COMMENT '실제 원인 (고객 비공개, 1~5,000자)',
  action         TEXT        NOT NULL COMMENT '실제 조치 (고객 공개, 1~5,000자)',
  result         TEXT        NOT NULL COMMENT '조치 결과 (고객 공개, 1~5,000자)',
  checked_values TEXT        NULL     COMMENT '[v2] 확인한 상태값 (측정치·센서 상태, 5,000자 이하)',
  PRIMARY KEY (case_id),
  CONSTRAINT fk_resolutions_service_cases
    FOREIGN KEY (case_id) REFERENCES service_cases (case_id)
) COMMENT='수리 결과 대장';

CREATE TABLE chat_turns (
  turn_id    INT           NOT NULL AUTO_INCREMENT COMMENT '조회는 이 순서',
  case_id    VARCHAR(64)   NOT NULL COMMENT '접수번호 (FK)',
  question   VARCHAR(2000) NOT NULL COMMENT '엔지니어 질문 (1~2,000자)',
  answer     JSON          NOT NULL COMMENT '답변. 보류 판정도 저장',
  created_at DATETIME      NOT NULL COMMENT '저장 시각 (UTC)',
  PRIMARY KEY (turn_id),
  CONSTRAINT fk_chat_turns_service_cases
    FOREIGN KEY (case_id) REFERENCES service_cases (case_id)
) COMMENT='대화 기록장';

-- ---------------------------------------------------------------------------
-- 아래는 ERD에는 그리지 않고 구현(schemas.py)에서 거는 CHECK 제약이다. 가져오기에는 넣지 않는다.
--
-- error_codes   : severity IN ('상','중','하'), action_owner IN ('고객사 조치','엔지니어 필요'), page >= 1
-- service_cases : source IN ('seed','reported'), status IN ('received','in_progress','completed'),
--                 route IN ('self_guide','support'),
--                 guide_failure IS NULL OR guide_failure IN ('held','generation_failed')
--                   -- no_customer_procedure를 열거형에 넣을지 팀 결정 전
--                 허용 조합(ck_service_cases_route_combo):
--                   (route='self_guide' AND guide_failure IS NULL AND guidance_answer IS NOT NULL AND status='completed')
--                   OR (route='support' AND guidance_answer IS NULL)
--                 ck_service_cases_completed_at:
--                   (status='completed' AND completed_at IS NOT NULL) OR (status<>'completed' AND completed_at IS NULL)
--                 ck_service_cases_time_order: completed_at IS NULL OR completed_at >= created_at
--                 memo: CHAR_LENGTH(TRIM(memo)) >= 1
-- ---------------------------------------------------------------------------
