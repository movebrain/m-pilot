## CHG-01 로그인 제거, 고객·엔지니어 앱 분리
- 날짜: 2026-10-26 / 결정: 노동균 / 확인: (영향 파트 담당 - 없음)
- 배경: 로그인 화면 없이 역할별로 접속을 분리하려고
- 내용: app.py → app_customer.py, app_engineer.py. ui/login.py, service.login, DemoAccount 삭제
- 영향받는 spec 절: §1.3 로그인, §2.2, §2.3.3, §2.4.2, §4 제약
- 대체: 없음