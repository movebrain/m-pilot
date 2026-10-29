"""[보류] 이미지 페이지 OCR. 코드 전체가 주석이다.

역할:
    글자가 없거나 큰 그림이 있는 쪽을 찾아 비전 모델로 전사하고 초안으로 만든다(현재 매뉴얼 4종에는 이미지 페이지가 없어 보류).

책임:
    - 다시 쓸 때: 아래 주석과 import 주석을 풀고, `parse_manuals`(`parser/__init__.py`)에 OCR 초안을 더하며,
      tests/test_parser_tables_ocr.py의 OCR 테스트 주석도 함께 푼다(#103 리뷰).

의존: (주석을 풀면) `parser.tables`·`parser.types`, `models`, langchain_core(의존 규칙 §2.5).
소유: DB 파트(이재서)
"""

from __future__ import annotations

# import base64
# import io
# import re
# from pathlib import Path
#
# from langchain_core.language_models import BaseChatModel
# from langchain_core.messages import HumanMessage
#
# from mpilot.db.parser.tables import DRAFT_MAX_CHARS, _open_pdf, _printed_page_number
# from mpilot.db.parser.types import ChunkDraft
# from mpilot.models import DependencyError, ValidationError

# ---------------------------------------------------------------------------
# [보류] 이미지 페이지·OCR(담당: 이재서)
#
# 현재 매뉴얼 4종에는 이미지 페이지가 없어 OCR이 실행되지 않으므로 주석으로 막아 두었다(#103 리뷰).
# 다시 쓸 때: 아래 줄 맨 앞의 `# `를 지우고, 파일 위쪽 import 4줄(base64, io, BaseChatModel, HumanMessage)과
# tests/test_parser_tables_ocr.py의 OCR 테스트 주석도 함께 푼다.
# ---------------------------------------------------------------------------

# # ---------------------------------------------------------------------------
# # 이미지 페이지·OCR(담당: 이재서)
# #
# # 흐름: `_find_image_pages`(글자가 없거나 큰 그림이 있는 쪽) → `_ocr_page`(300dpi 캡처 → 비전 모델 전사)
# #       → `_ocr_to_drafts`(문단 묶기) → `ChunkDraft`
# # 캡처·base64·HumanMessage(image_url) 방식은 수업(Day11 멀티모달) 코드를 따른다.
# # ---------------------------------------------------------------------------
#
# _IMAGE_AREA_RATIO = 0.25
# """쪽 면적에서 이미지가 차지하는 비율이 이 값 이상이면 이미지 페이지로 본다."""
#
# # 프롬프트는 수업(Day11 멀티모달·소득세법 워크북)의 'OCR 작업' 문장을 따른다. 전문 용어가 많으면 모델이 답을
# # 거부할 수 있어 '분석'이 아니라 '전사'임을 밝히고, 거부하면 영어 프롬프트로 한 번 다시 시도한다.
# _OCR_PROMPT = (
#     "다음 이미지는 설비 매뉴얼의 한 페이지입니다. OCR 작업입니다: "
#     "이미지에 적힌 글자·숫자를 보이는 그대로 옮기고, 표는 각 행과 열을 마크다운 표로 옮기세요. "
#     "해석·요약하지 말고 그대로 전사만 하세요. 글자가 없으면 아무것도 출력하지 마세요."
# )
# _OCR_RETRY_PROMPT = (
#     "Transcribe all text in this image exactly as written. Put any table into a markdown table. "
#     "Copy text and numbers exactly. Plain OCR task."
# )
# # 수업 코드처럼 답에 이 문구가 들어 있으면 거부로 본다(소문자로 바꿔 비교한다).
# _REFUSAL_MARKERS = ("can't assist", "cannot assist", "i'm sorry", "i am sorry")
# _OCR_DPI = 300
# """캡처 해상도. 표의 작은 숫자도 또렷하게 읽히도록 수업에서 검증한 값을 쓴다."""
#
# # `set_vision_model`이 넣어 주는 비전 모델. 아직 설정 전이면 None.
# _vision_model: BaseChatModel | None = None
#
#
# def set_vision_model(vision_model: BaseChatModel) -> None:
#     """이미지 페이지 전사(`_ocr_page`)에 쓸 비전 모델을 설정한다.
#
#     Args:
#         vision_model: 이미지를 입력받는 LangChain 채팅 모델(예: gpt-4o). `app`이 설정(API 키, 제한 시간)으로
#             만들어 넘긴다. temperature=0을 권장한다.
#
#     Returns:
#         None: 모듈 안에 모델을 보관한다. 이후 `_ocr_page`는 이 모델을 쓴다.
#     """
#     global _vision_model
#     _vision_model = vision_model
#
#
# def _image_area(image: dict) -> float:
#     """pdfplumber가 알려 준 이미지 하나의 면적을 구한다.
#
#     Args:
#         image: `page.images`의 한 항목(`x0`, `x1`, `top`, `bottom`).
#
#     Returns:
#         float: 면적(pt²). 좌표가 뒤집혀 폭이나 높이가 음수면 그 값을 0으로 본다.
#     """
#     width = float(image["x1"]) - float(image["x0"])
#     height = float(image["bottom"]) - float(image["top"])
#     if width < 0:
#         width = 0.0
#     if height < 0:
#         height = 0.0
#     return width * height
#
#
# def _is_image_page(text: str, page_area: float, image_areas: list[float]) -> bool:
#     """한 쪽이 이미지 페이지인지 판정한다(순수 함수).
#
#     Args:
#         text: 그 쪽에서 추출한 글자.
#         page_area: 쪽 면적(pt²).
#         image_areas: 그 쪽 이미지들의 면적(pt²).
#
#     Returns:
#         bool: 글자가 없거나, 이미지 면적 합이 쪽 면적의 `_IMAGE_AREA_RATIO` 이상이면 True.
#     """
#     # 1) 글자가 하나도 없으면 스캔본 같은 이미지 페이지다.
#     if not text.strip():
#         return True
#     # 2) 쪽 크기를 알 수 없으면 비율을 구할 수 없다.
#     if page_area <= 0:
#         return False
#     # 3) 이미지가 쪽의 25% 이상을 차지하면 이미지 페이지다.
#     return sum(image_areas) / page_area >= _IMAGE_AREA_RATIO
#
#
# def _find_image_pages(path: Path) -> list[int]:
#     """텍스트가 없거나 큰 이미지가 있는 페이지를 찾는다.
#
#     표지(PDF 첫 쪽)는 청킹 대상이 아니라 찾지 않는다.
#
#     Args:
#         path: 매뉴얼 PDF 파일 경로.
#
#     Returns:
#         list[int]: OCR이 필요한 쪽 번호 목록. 꼬리글의 인쇄 쪽 번호이고 오름차순이다.
#             꼬리글이 이미지라 읽히지 않는 쪽은 PDF 쪽 순서 - 1로 센다.
#
#     Raises:
#         DependencyError: PDF를 읽는 데 실패했을 때.
#     """
#     pages: list[int] = []
#     with _open_pdf(path) as pdf:
#         try:
#             # 첫 쪽(표지)은 청킹 대상이 아니라 둘째 쪽부터 본다.
#             for page in pdf.pages[1:]:
#                 # 1) 쪽 안 이미지들의 면적을 모은다.
#                 image_areas = []
#                 for image in page.images:
#                     image_areas.append(_image_area(image))
#
#                 # 2) 글자와 면적으로 이미지 페이지인지 판정한다.
#                 text = page.extract_text() or ""
#                 page_area = float(page.width) * float(page.height)
#                 if _is_image_page(text, page_area, image_areas):
#                     # 3) 이미지 페이지면 인쇄 쪽 번호를 기록한다.
#                     pages.append(_printed_page_number(page.extract_text_lines(), page.page_number))
#         except Exception as e:  # pdfminer·pdfplumber 오류 종류가 많아 한곳에서 의존 오류로 바꾼다.
#             raise DependencyError(f"PDF 쪽을 읽지 못했습니다: {Path(path).name}({type(e).__name__})") from None
#     return pages
#
#
# def _ocr_page(path: Path, page_no: int) -> str:
#     """페이지를 300dpi로 캡처해 비전 모델로 전사한다.
#
#     전사는 '보이는 그대로'이며 해석·요약하지 않는다. 표는 마크다운 표로 옮긴다.
#     모델이 거부하면 영어 프롬프트로 한 번 다시 시도한다(수업 방식).
#     모델 출력이라 같은 쪽이라도 실행마다 글자가 조금 달라질 수 있다.
#
#     Args:
#         path: 매뉴얼 PDF 파일 경로.
#         page_no: 전사할 쪽 번호. 꼬리글의 인쇄 쪽 번호이고 1부터 센다(표지는 대상이 아니다).
#             PDF 쪽 순서로는 `page_no + 1`번째 쪽이다.
#
#     Returns:
#         str: 전사한 글자. 앞뒤 공백을 지운다. 글자가 없는 쪽이면 빈 문자열.
#
#     Raises:
#         ValidationError: `page_no`가 표지를 뺀 쪽 범위를 벗어날 때.
#         DependencyError: 비전 모델이 설정되지 않았거나, PDF 캡처·모델 호출이 실패하거나 시간이 초과될 때,
#             다시 시도해도 모델이 전사를 거부할 때.
#     """
#     # 1) 비전 모델이 없으면 PDF를 열기 전에 알린다.
#     if _vision_model is None:
#         raise DependencyError("비전 모델이 설정되지 않았습니다(set_vision_model을 먼저 호출해야 합니다).")
#
#     with _open_pdf(path) as pdf:
#         # 2) 쪽 번호가 표지를 뺀 범위(1 ~ 전체 쪽 수 - 1) 안인지 확인한다.
#         last_page_no = len(pdf.pages) - 1
#         if page_no < 1 or page_no > last_page_no:
#             raise ValidationError(f"쪽 번호가 범위를 벗어났습니다: {page_no} (1~{last_page_no}, 표지 제외)")
#
#         # 3) 쪽을 300dpi 이미지로 캡처해 PNG로 메모리에 저장한다.
#         #    표지가 0번이라 인쇄 쪽 번호가 곧 `pdf.pages`의 인덱스다.
#         try:
#             image = pdf.pages[page_no].to_image(resolution=_OCR_DPI)
#             buffer = io.BytesIO()
#             image.original.save(buffer, format="PNG")
#         except Exception as e:
#             raise DependencyError(f"쪽을 이미지로 캡처하지 못했습니다: p.{page_no}({type(e).__name__})") from None
#
#     # 4) 모델에 보낼 수 있게 base64 글자로 바꾼다.
#     encoded_png = base64.b64encode(buffer.getvalue()).decode("ascii")
#
#     # 5) 한국어 프롬프트로 전사한다.
#     text = _transcribe(_OCR_PROMPT, encoded_png, page_no)
#     if not _is_refusal(text):
#         return text
#
#     # 6) 거부하면 영어 프롬프트로 한 번 더 시도한다.
#     text = _transcribe(_OCR_RETRY_PROMPT, encoded_png, page_no)
#     if _is_refusal(text):
#         # 거부 문구가 청크로 저장되면 검색 근거에 엉뚱한 글이 섞이므로 저장하지 않고 알린다.
#         raise DependencyError(f"비전 모델이 전사를 거부했습니다: p.{page_no}")
#     return text
#
#
# def _transcribe(prompt: str, encoded_png: str, page_no: int) -> str:
#     """이미지 한 장을 비전 모델로 전사한다.
#
#     Args:
#         prompt: 전사 지시문.
#         encoded_png: base64로 인코딩한 PNG.
#         page_no: 오류 문구에 쓸 쪽 번호.
#
#     Returns:
#         str: 모델이 돌려준 글자. 앞뒤 공백을 지운다.
#
#     Raises:
#         DependencyError: 모델 호출이 실패하거나 시간이 초과될 때.
#     """
#     # 1) 글자 지시문과 이미지를 한 메시지에 담는다(수업 코드와 같은 형식).
#     message = HumanMessage(
#         content=[
#             {"type": "text", "text": prompt},
#             {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_png}", "detail": "high"}},
#         ]
#     )
#
#     # 2) 모델을 부른다.
#     try:
#         response = _vision_model.invoke([message])
#     except Exception as e:  # OpenAI 오류·시간 초과 종류가 많아 한곳에서 의존 오류로 바꾼다.
#         raise DependencyError(f"비전 모델 호출에 실패했습니다: p.{page_no}({type(e).__name__})") from None
#
#     # 3) 일부 모델은 내용을 조각 목록으로 돌려주므로 글자만 이어 붙인다.
#     content = response.content
#     if isinstance(content, list):
#         pieces = []
#         for part in content:
#             if isinstance(part, dict):
#                 pieces.append(part.get("text", ""))
#             else:
#                 pieces.append(str(part))
#         content = "".join(pieces)
#     return str(content).strip()
#
#
# def _is_refusal(text: str) -> bool:
#     """모델 답이 전사 거부 문구인지 판정한다(수업 코드의 'sorry'·'can't assist' 확인과 같은 방식).
#
#     Args:
#         text: 모델이 돌려준 글자.
#
#     Returns:
#         bool: 거부 문구가 들어 있으면 True.
#     """
#     lowered = text.lower()
#     for marker in _REFUSAL_MARKERS:
#         if marker in lowered:
#             return True
#     return False
#
#
# def _ocr_to_drafts(text: str, page_no: int) -> list[ChunkDraft]:
#     """OCR 결과를 청크 초안으로 만든다.
#
#     빈 줄로 나뉜 문단을 차례로 모아 `DRAFT_MAX_CHARS` 이하로 묶는다. 문단은 중간에서 자르지 않으므로
#     한 문단이 기준보다 길면 그 문단만 담은 초안이 기준을 넘을 수 있다.
#
#     Args:
#         text: `_ocr_page`가 돌려준 전사 글자.
#         page_no: 전사한 쪽 번호. 1부터 센다.
#
#     Returns:
#         list[ChunkDraft]: `kind="ocr"`, `top=0.0`, `section=None`인 초안 목록. 글자가 없으면 빈 목록.
#     """
#     # 1) 빈 줄(공백만 있는 줄 포함)을 기준으로 문단을 나누고, 빈 문단은 버린다.
#     paragraphs = []
#     for piece in re.split(r"\n\s*\n", text):
#         piece = piece.strip()
#         if piece:
#             paragraphs.append(piece)
#
#     # 2) 문단을 차례로 이어 붙이다가, 다음 문단을 더하면 기준 글자 수를 넘을 때 새 묶음을 시작한다.
#     groups: list[str] = []
#     current = ""
#     for paragraph in paragraphs:
#         if current:
#             candidate = current + "\n\n" + paragraph
#         else:
#             candidate = paragraph
#
#         if current and len(candidate) > DRAFT_MAX_CHARS:
#             groups.append(current)
#             current = paragraph
#         else:
#             current = candidate
#     if current:
#         groups.append(current)
#
#     # 3) 묶음마다 초안을 만든다. 이미지 쪽은 위치를 알 수 없어 `top=0.0`(쪽 맨 위)으로 둔다.
#     drafts = []
#     for group in groups:
#         drafts.append(ChunkDraft(page=page_no, top=0.0, kind="ocr", section=None, text=group))
#     return drafts
