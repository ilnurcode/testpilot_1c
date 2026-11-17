"""Страж полноты .env.example: переменные, которые читает код, должны быть документированы.

Сбор идёт по AST (os.environ.get/os.getenv/environ[...]/environ.get) плюс по строковым
литералам имён с проектными префиксами — часть переменных читается динамически
(например таблица ролей `TP_ANALYST_PASSWORD` в testpilot/common.py).
Проверка односторонняя: переменная в .env.example, которую код не читает, не ловится.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).parent.parent / 'testpilot'
EXAMPLE = Path(__file__).parent.parent / '.env.example'
ENV_NAME_PREFIXES = ('TP', 'POSTGRES', 'CODEX', 'DATABASE', 'IMPACT', 'INTELLIGENCE', 'WEB')
_LITERAL = re.compile(r'^(?:' + '|'.join(ENV_NAME_PREFIXES) + r')_[A-Z0-9_]+$')
_MIN_COLLECTED = 20

# Системные каталоги Codex CLI и общие переменные окружения ОС: пользователь их не настраивает.
EXCEPTIONS = {
    'LOCALAPPDATA': 'системный каталог Windows, из него код выводит путь к credentials Codex',
    'XDG_DATA_HOME': 'системный каталог Linux, из него код выводит путь к credentials Codex',
    'PATH': 'системная переменная ОС',
    'HOME': 'системная переменная ОС',
    'HOSTNAME': 'системная переменная ОС',
}


def _is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == 'environ'
    return isinstance(node, ast.Name) and node.id == 'environ'


class _EnvReader(ast.NodeVisitor):
    def __init__(self, file: str) -> None:
        self.file = file
        self.found: dict[str, str] = {}

    def _note(self, name: str, line: int) -> None:
        self.found.setdefault(name, f'{self.file}:{line}')

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {'get', 'getenv', 'setdefault', 'pop'}:
            if _is_environ(func.value) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    self._note(first.value, node.lineno)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_environ(node.value):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self._note(key.value, node.lineno)
        self.generic_visit(node)


def _collected_variables() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(SOURCE_ROOT.rglob('*.py')):
        text = path.read_text(encoding='utf-8')
        tree = ast.parse(text)
        reader = _EnvReader(path.as_posix())
        reader.visit(tree)
        found.update(reader.found)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _LITERAL.match(node.value):
                found.setdefault(node.value, f'{path.as_posix()}:{node.lineno}')
    return found


def _example_keys() -> tuple[set[str], list[str]]:
    lines = EXAMPLE.read_text(encoding='utf-8').splitlines()
    keys: set[str] = set()
    uncommented: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)="?', line)
        if not match:
            continue
        key = match.group(1)
        keys.add(key)
        inline_comment = '#' in line.split('=', 1)[1]
        preceded = any(item.strip().startswith('#') for item in lines[max(0, index - 3):index])
        if not (inline_comment or preceded):
            uncommented.append(key)
    return keys, uncommented


def test_env_vars_read_by_code_are_documented_in_env_example():
    collected = _collected_variables()
    keys, uncommented = _example_keys()

    assert len(collected) >= _MIN_COLLECTED, (
        f'собрано подозрительно мало переменных ({len(collected)}): сломался разбор исходников'
    )

    missing = sorted(name for name in collected if name not in keys and name not in EXCEPTIONS)
    assert not missing, 'нет в .env.example: ' + ', '.join(missing)

    assert not uncommented, 'в .env.example без комментария: ' + ', '.join(sorted(uncommented))
