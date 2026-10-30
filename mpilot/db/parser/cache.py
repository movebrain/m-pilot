"""매뉴얼 청크 목록의 디스크 캐시(#161).

역할:
    `parse_manuals`가 만든 청크 목록을 JSON 파일로 저장하고, 입력이 같으면 다음 실행에서 그 파일을 불러온다.
    앱 시작 시간의 대부분이 PDF 파싱(4종에 약 220초)이라, 입력이 그대로면 파싱을 건너뛰려는 것이다.

책임:
    - 지문(fingerprint)에 PDF 파일 이름·내용, `parser` 패키지 소스, `pdfplumber` 버전을 넣는다.
      하나라도 바뀌면 지문이 달라져 다시 파싱한다. 같은 입력이면 청크가 늘 같다는 parser 재현성에 기대는 것이다.
    - 캐시 파일은 매뉴얼 폴더 옆의 `cache/` 폴더(`data/manuals` → `data/cache`)에 둔다. 저장소에 올리지 않는다(`.gitignore`).
    - 캐시 파일이 없거나 깨졌으면(`Chunk` 필드가 바뀐 경우 포함) 없는 것으로 본다. 오류를 내지 않는다.
    - 캐시를 읽거나 쓰지 못해도 앱 시작을 막지 않는다. 그때는 캐시 없이 파싱한 결과를 그대로 쓴다.
    - 벡터·Chroma는 저장하지 않는다. 색인은 지금처럼 시작마다 새로 만든다(spec §2.1·§3.4).

의존: `models`, `pdfplumber`(버전만)를 import한다(의존 규칙 §2.5).
소유: DB 파트(손재호)
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path

import pdfplumber
from pydantic import TypeAdapter

from mpilot.models import Chunk

CACHE_DIR_NAME = "cache"  # 매뉴얼 폴더와 같은 부모 아래에 만든다
CACHE_FILE_PREFIX = "manual_chunks"  # 캐시 파일 이름 앞부분. 지문이 바뀌면 같은 앞부분의 옛 파일을 지운다

_CHUNKS = TypeAdapter(list[Chunk])  # 청크 목록 ↔ JSON


def _cache_file(directory: Path, pdf_paths: list[Path]) -> Path | None:
    """이번 입력에 해당하는 캐시 파일 경로를 정한다.

    Args:
        directory: 매뉴얼 PDF 폴더. 캐시 폴더는 이 폴더의 부모 아래 `cache/`다.
        pdf_paths: 파싱할 PDF 경로(파일 이름순).

    Returns:
        Path | None: `<부모>/cache/manual_chunks-<지문>.json`. PDF를 읽지 못해 지문을 만들 수 없으면 None(캐시를 쓰지 않는다).
    """
    try:
        fingerprint = _fingerprint(pdf_paths)
    except OSError:  # PDF를 못 읽으면 캐시 없이 파싱으로 넘겨, 파싱 쪽이 알맞은 오류를 내게 한다
        return None
    return directory.parent / CACHE_DIR_NAME / f"{CACHE_FILE_PREFIX}-{fingerprint}.json"


def _fingerprint(pdf_paths: list[Path]) -> str:
    """캐시 지문을 만든다. 청크 결과를 바꿀 수 있는 입력을 모두 넣는다.

    Args:
        pdf_paths: 파싱할 PDF 경로(파일 이름순).

    Returns:
        str: SHA-256 앞 16자리. PDF 이름·내용, `parser` 패키지의 모든 `.py`, `pdfplumber` 버전 중 하나라도 바뀌면 달라진다.

    Raises:
        OSError: PDF나 소스 파일을 읽지 못했을 때.
    """
    digest = hashlib.sha256()
    for path in pdf_paths:  # 파일 이름순. 이름도 넣어 파일을 바꿔 끼운 경우를 가린다
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    for source in sorted(Path(__file__).parent.glob("*.py")):  # 이 패키지의 어느 파일이 바뀌어도 다시 파싱한다
        digest.update(source.name.encode())
        digest.update(hashlib.sha256(source.read_bytes()).digest())
    digest.update(pdfplumber.__version__.encode())  # 같은 PDF라도 라이브러리 버전에 따라 읽는 결과가 다를 수 있다
    return digest.hexdigest()[:16]


def _load_chunks(file: Path) -> list[Chunk] | None:
    """캐시 파일에서 청크 목록을 불러온다.

    Args:
        file: `_cache_file`이 정한 캐시 파일.

    Returns:
        list[Chunk] | None: 저장된 청크 목록. 파일이 없거나 깨졌거나 `Chunk` 형식과 맞지 않으면 None.
    """
    try:
        return _CHUNKS.validate_json(file.read_bytes())
    except (OSError, ValueError):  # 파일 없음·읽기 실패, 깨진 JSON·필드 불일치(pydantic 검증 오류도 ValueError다)
        return None


def _save_chunks(file: Path, chunks: list[Chunk]) -> None:
    """청크 목록을 캐시 파일로 저장하고, 지문이 달라 못 쓰게 된 옛 캐시 파일을 지운다.

    쓰는 도중 끊겨도 반쪽 파일이 남지 않도록 임시 파일에 쓴 뒤 이름을 바꾼다.

    Args:
        file: `_cache_file`이 정한 캐시 파일.
        chunks: 방금 파싱한 청크 목록.

    Returns:
        None: 저장에 실패해도(읽기 전용 디스크 등) 오류를 내지 않는다. 다음 실행에서 다시 파싱할 뿐이다.
    """
    temp = file.with_suffix(".tmp")
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        for old in file.parent.glob(f"{CACHE_FILE_PREFIX}-*.json"):  # 입력이 바뀌어 다시 쓰일 일이 없는 것
            if old != file:
                old.unlink()
        temp.write_bytes(_CHUNKS.dump_json(chunks))
        os.replace(temp, file)  # 같은 폴더 안 이름 바꾸기라 한 번에 바뀐다
    except OSError:  # 캐시만 못 남기고 앱 시작은 계속한다
        with contextlib.suppress(OSError):  # 반쪽 임시 파일이 남았으면 지운다
            temp.unlink(missing_ok=True)
