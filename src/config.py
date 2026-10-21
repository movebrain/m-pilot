"""`.env`를 읽고 LLM·임베딩을 만든다. 소유: 서비스 이력.

search·answer·history·indexing은 LLM·임베딩을 이 두 함수로만 얻고, `config.get_llm()`처럼 모듈을 거쳐 부른다
(테스트의 fake_llm 픽스처가 이 두 함수를 바꾼다). 객체는 처음 부를 때 만들어 import만으로는 API에 접속하지 않는다.
키는 OPENAI_API_KEY(langchain-openai가 직접 읽는다), 모델명은 OPENAI_CHAT_MODEL·OPENAI_EMBEDDING_MODEL이다.
"""
import os
from functools import cache

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

DEFAULT_CHAT_MODEL = "gpt-4o-mini"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


def _load_env() -> None:
    from dotenv import load_dotenv
    load_dotenv()


@cache
def get_llm() -> BaseChatModel:
    """채팅 모델(temperature=0, spec 3장 재현성)."""
    _load_env()
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=os.getenv("OPENAI_CHAT_MODEL") or DEFAULT_CHAT_MODEL, temperature=0)


@cache
def get_embeddings() -> Embeddings:
    """임베딩 모델. Chroma manual·history에 같은 모델을 쓴다."""
    _load_env()
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(model=os.getenv("OPENAI_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL)
