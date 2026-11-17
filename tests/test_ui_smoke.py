"""UI smoke: разделы интерфейса открываются без JS-ошибок и битого текста.

Запускается только когда задан TP_UI_BASE_URL (например http://127.0.0.1:8000);
без него и без Chromium тест пропускается. В приёмке переменную выставляет scripts/acceptance.sh.
"""
from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.ui

BASE = (os.environ.get('TP_UI_BASE_URL') or '').rstrip('/')
EXPERT = os.environ.get('TP_EXPERT_PASSWORD') or ''
BAD_TEXT = re.compile(r'\bundefined\b|\bNaN\b|\[object Object\]')
SECTION_TIMEOUT_MS = 20000
SECTIONS = ('overview', 'projects', 'audit', 'ai')


def _require_ui_env() -> None:
    if not BASE:
        pytest.skip('TP_UI_BASE_URL не задан — UI smoke пропущен (например http://127.0.0.1:8000)')
    if not EXPERT:
        pytest.skip('TP_EXPERT_PASSWORD не задан — UI smoke пропущен')
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:  # pragma: no cover - зависит от окружения
        pytest.skip('playwright не установлен — UI smoke пропущен')


@pytest.fixture(scope='module')
def browser(  # noqa: ANN201
):
    _require_ui_env()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            launched = p.chromium.launch()
        except Exception as error:  # pragma: no cover - зависит от окружения
            pytest.skip(f'Chromium недоступен ({error}) — UI smoke пропущен')
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture
def ui(browser):  # noqa: ANN001, ANN201
    context = browser.new_context(http_credentials={'username': 'expert', 'password': EXPERT})
    page = context.new_page()
    page.set_default_timeout(SECTION_TIMEOUT_MS)
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
    page.on('pageerror', lambda error: page_errors.append(str(error)))
    state = {'console': console_errors, 'page_errors': page_errors}
    try:
        yield page, state
    finally:
        context.close()


def _open(page, state, section: str, ready: str) -> None:  # noqa: ANN001
    try:
        page.goto(f'{BASE}/#{section}', wait_until='networkidle')
        page.wait_for_selector(ready)
    except Exception as error:
        tail = '\n'.join(state['console'][-5:] + state['page_errors'][-5:]) or '(сообщений нет)'
        raise AssertionError(
            f'раздел #{section} ({page.url}) не отрисовался: ожидался элемент {ready!r}; {error}\n'
            f'последние сообщения:\n{tail}'
        ) from None


def _assert_clean(page, state, section: str) -> None:  # noqa: ANN001
    text = page.inner_text('#content')
    bad = sorted(set(BAD_TEXT.findall(text)))
    if bad or state['console'] or state['page_errors']:
        tail = '\n'.join(state['console'][-5:] + state['page_errors'][-5:])
        raise AssertionError(
            f'раздел #{section} ({page.url}) содержит дефекты: '
            f'битый текст={bad}, console={state["console"]}, pageerror={state["page_errors"]}\n'
            f'последние сообщения:\n{tail}'
        )


@pytest.mark.parametrize('section,ready,expected', [
    ('overview', '#content .stats > div', 'Что изменилось и что проверить'),
    ('projects', '.project-card', 'Проекты и файлы'),
    ('audit', '#auditJournalCard', 'Аудит BSL и стандарты v8std'),
    ('ai', '#saveLlmSettings', 'Нейросеть и проверки'),
])
def test_ui_section_renders(ui, section, ready, expected):
    page, state = ui
    _open(page, state, section, ready)
    assert expected in page.inner_text('#content h1'), f'#{section}: нет заголовка {expected!r}'
    _assert_clean(page, state, section)


def test_ui_overview_has_stats_and_no_js_crash(ui):
    page, state = ui
    _open(page, state, 'overview', '#content .stats > div')

    stats = page.eval_on_selector_all('#content .stats > div', 'elements => elements.length')
    assert stats >= 1, 'обзор: нет ни одного блока статистики'
    message = page.inner_text('#message')
    assert 'is not a function' not in message, f'обзор: JS-падение в #message: {message!r}'
    _assert_clean(page, state, 'overview')


def test_ui_project_page_opens(ui):
    page, state = ui
    _open(page, state, 'projects', '.project-card')
    page.eval_on_selector('.project-card .project-open', 'element => element.click()')
    page.wait_for_selector('#deleteProjectDialog', state='attached')

    assert page.inner_text('#content h1').strip(), 'проект: пустой заголовок'
    _assert_clean(page, state, 'project')
