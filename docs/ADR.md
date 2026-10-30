## 문서와 ADR이 다르면 ADR이 우선한다

## CHG-01 로그인 제거, 고객·엔지니어 앱 분리
- 날짜: 2026-10-26 / 결정: 노동균 / 확인: (영향 파트 담당 - 없음)
- 배경: 로그인 화면 없이 역할별로 접속을 분리하려고
- 내용: app.py → app_customer.py, app_engineer.py. ui/login.py, service.login, DemoAccount 삭제
- 영향받는 spec 절: §1.3 로그인, §2.2, §2.3.3, §2.4.2, §4 제약
- 대체: 없음

## CHG-02 고객 신고 사진 첨부 제거
- 날짜: 2026-10-27 / 결정: 노동균 / 확인: (영향 파트 담당 - 손재호: 신고 저장)
- 배경: 사진은 분기·RAG에 쓰지 않고 엔지니어 열람용이라 핵심 흐름에 영향이 없고, 파일 검증·저장·열람 구현 비용에 비해 시연 가치가 낮아서
- 내용: 신고 작성 화면의 사진 업로드 영역 삭제. 고객 신고 상세의 사진 영역, 엔지니어 대화창의 '사진 첨부' 링크 삭제. PhotoUpload, photo_path 필드(CaseInput·ServiceCase·CustomerCaseResult), storage/photos 저장·삭제 함수, 사진 형식·용량 검증(jpg/png, 5MB) 삭제
- 영향받는 spec 절: §?.? (신고 입력), §?.? (데이터 모델), §?.? (화면: 고객 신고 작성·상세, 엔지니어 대화창), §4 제약 (파일 형식·용량)
- 대체: 증상은 증상 메모(텍스트)로만 전달

## CHG-03 셀프 가이드 해결 확인(해결완료·기술지원접수)과 엔지니어 이관
- 날짜: 2026-10-30 / 결정: (승인 전) / 확인: (영향 파트 담당 - 손재호: 신고 저장, 이재서: 대화·조치, UI 담당)
- 배경: 셀프 가이드 건이 접수 즉시 조치 완료로 닫혀, 안내로 해결되지 않은 고객이 엔지니어에게 이어질 방법이 없고 엔지니어 대시보드에서도 열 수 없어서
- 내용: 셀프 가이드 건은 `in_progress`로 접수한다. 고객 상세의 [해결완료]는 `completed`로 바꾸고(`rdb.resolve_self_guide_case`), [기술지원접수]는 `in_progress`를 유지한 채 `route=support`, `guide_failure=unresolved`로 이관한다(`rdb.escalate_self_guide_case`, 안내 `guidance_answer`는 남긴다). 엔지니어 대시보드는 모든 건을 열 수 있고, 완료 건과 고객 확인 전 셀프 가이드 건은 대화 화면이 읽기 전용이며 이관된 건은 질문·조치 등록이 된다. `CustomerCaseResult`에 `route`를 추가하고 `service.resolve_self_guide`·`service.request_support`를 새로 둔다. 완료 후 채팅·재개는 그대로 범위 밖이다.
- 영향받는 spec 절: §1.3(고객 신고 상세, 엔지니어 대시보드), §2.3.1, §2.3.3, §2.4.2(`GuideFailure`, `CustomerCaseResult`, `InvalidStateError`), §2.10(상태 전이·허용 조합·이력 취급), §3.3(`guide_failure`·`self_guide_answer`), §4 범위·제약
- 대체: spec §2.10의 "셀프 가이드는 접수 때 `completed`", §4 제약의 "자동 안내 완료는 안내 제공의 종료"
