"""Run with: python -m database.init_db"""
from pathlib import Path
from .store import Database


def main():
    # schema.sql owns its BEGIN/COMMIT transaction.
    with Database().connect() as conn:
        conn.autocommit = True
        conn.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    print('Database tables are ready.')


if __name__ == '__main__':
    main()
