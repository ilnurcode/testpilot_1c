"""Страж конфигурации pytest: `-q` не в addopts, `--strict-markers` не потерян.

`-q` в addopts вместе с `-q` из приёмки/CI даёт `-qq`, и pytest перестаёт печатать строку
«N passed», которую парсит `scripts/acceptance.sh`.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / 'pyproject.toml'
QUIET_MESSAGE = (
    "`-q` в addopts делает `-qq` при вызове из приёмки/CI: pytest перестаёт печатать строку "
    "„N passed“, которую парсит `scripts/acceptance.sh` — оставьте `-q` вызывающей стороне"
)


def _addopts() -> list[str]:
    config = tomllib.loads(PYPROJECT.read_text(encoding='utf-8'))
    raw = config['tool']['pytest']['ini_options'].get('addopts', '')
    return raw.split()


def test_addopts_has_no_quiet_flag():
    tokens = _addopts()
    quiet = [token for token in tokens if token == '--quiet' or (token.startswith('-q') and set(token[1:]) == {'q'})]

    assert not quiet, f'{QUIET_MESSAGE} (найдено: {quiet})'


def test_addopts_keeps_strict_markers():
    assert '--strict-markers' in _addopts(), 'в addopts пропал --strict-markers: опечатка в маркере не будет ошибкой'
