"""Создаёт локальные случайные секреты в каталоге данных; значения никогда не выводятся.

Каталог данных: TP_DATA_DIR, иначе текущий каталог (checkout или каталог установки).
Пакет неизменяем: для установки в Program Files задайте TP_DATA_DIR вне него.
"""
from pathlib import Path
import os
import secrets

data_dir = Path(os.environ.get('TP_DATA_DIR') or '.').resolve()
data_dir.mkdir(parents=True, exist_ok=True)
p = data_dir / '.env'
if p.exists():
    print(f'.env существует в {data_dir}; сохранён без изменений')
else:
    password = secrets.token_urlsafe(24)
    p.write_text(
        f'POSTGRES_USER=testpilot\nPOSTGRES_DB=testpilot\nPOSTGRES_PASSWORD={password}\n'
        f'DATABASE_URL=postgresql+psycopg://testpilot:{password}@127.0.0.1:5432/testpilot\n'
        f'TP_ANALYST_PASSWORD={secrets.token_urlsafe(24)}\n'
        f'TP_EXPERT_PASSWORD={secrets.token_urlsafe(24)}\n'
        f'TP_RUNNER_PASSWORD={secrets.token_urlsafe(24)}\n'
        'IMPACT_URL=http://127.0.0.1:8001\nINTELLIGENCE_URL=http://127.0.0.1:8002\n'
    )
    try:
        p.chmod(0o600)
    except OSError:
        # Windows: chmod не даёт POSIX-изоляции; доступ ограничивается правами каталога.
        pass
    print(f'Создан .env в {data_dir} с локальными случайными секретами (не включается в Git)')
