"""Создаёт локальные случайные секреты; значения никогда не выводятся."""
from pathlib import Path
import secrets
p=Path('.env')
if p.exists():
    print('.env существует; сохранён без изменений')
else:
    password=secrets.token_urlsafe(24)
    p.write_text(f'POSTGRES_USER=testpilot\nPOSTGRES_DB=testpilot\nPOSTGRES_PASSWORD={password}\nDATABASE_URL=postgresql+psycopg://testpilot:{password}@127.0.0.1:5432/testpilot\nTP_ANALYST_PASSWORD={secrets.token_urlsafe(24)}\nTP_EXPERT_PASSWORD={secrets.token_urlsafe(24)}\nTP_RUNNER_PASSWORD={secrets.token_urlsafe(24)}\nIMPACT_URL=http://127.0.0.1:8001\nINTELLIGENCE_URL=http://127.0.0.1:8002\n')
    p.chmod(0o600)
    print('Создан .env с локальными случайными секретами (не включается в Git)')
