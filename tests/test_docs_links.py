"""Страж относительных ссылок в документации: каждая ведёт на существующий файл."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).parent.parent
LINK = re.compile(r'\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)')
EXTERNAL_PREFIXES = ('http://', 'https://', 'mailto:', 'tel:', 'ftp://', '#', 'data:')


def broken_links(root: Path, files: list[Path] | None = None) -> list[str]:
    """Возвращает «файл:строка — ссылка — чего нет» для всех относительных ссылок без цели."""
    if files is None:
        files = sorted(ROOT.glob('*.md')) + sorted((ROOT / 'docs').rglob('*.md'))
    problems: list[str] = []
    for path in files:
        base = path.parent
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            for raw in LINK.findall(line):
                target = raw.strip('<>').split('#', 1)[0].strip()
                if not target or target.startswith(EXTERNAL_PREFIXES):
                    continue
                if target.startswith('//'):
                    continue
                candidate = (base / unquote(target)).resolve()
                if not candidate.exists():
                    problems.append(f'{path.relative_to(root)}:{number} — {raw} — нет {candidate}')
    return problems


def test_repository_markdown_links_resolve():
    problems = broken_links(ROOT)

    assert not problems, 'битые относительные ссылки:\n' + '\n'.join(problems)


def test_broken_link_is_detected_in_temporary_tree(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'present.md').write_text('ссылка [есть](present.md) и [якорь](present.md#часть)', encoding='utf-8')
    (tmp_path / 'docs' / 'broken.md').write_text(
        'текст\n[цель](missing.md)\n[внешняя](https://example.com/x)\n', encoding='utf-8'
    )

    problems = broken_links(tmp_path, [tmp_path / 'present.md', tmp_path / 'docs' / 'broken.md'])

    assert len(problems) == 1, problems
    assert 'broken.md:2' in problems[0]
    assert 'missing.md' in problems[0]
