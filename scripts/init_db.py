"""스키마·시드 적재(python -m scripts.init_db). --reset은 시드 상태로 되돌리고 --check는 이력 시드를 검증한다(spec 5.3).

DDL은 scripts/schema.sql에, 쿼리는 src/db.py에 둔다. 소유: MySQL.
"""
import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MySQL 스키마·가상 이력 시드 적재")
    parser.add_argument("--reset", action="store_true", help="테이블을 비우고 시드 상태로 다시 적재한다")
    parser.add_argument("--check", action="store_true", help="history_seed.csv의 코드·형식만 검증한다")
    parser.parse_args(argv)
    raise NotImplementedError


if __name__ == "__main__":
    main()
