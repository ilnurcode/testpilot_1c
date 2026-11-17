"""Read-only зонд формы входа: только разметка, без сети, кликов и fill."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from testpilot.integration.webclient import collect_login_facts

FORM_HTML = """
<div id="authWindow">
  <input id="userField" name="userName" type="text" value="ТайныйЛогин" placeholder="Пользователь">
  <input id="pwdField" type="password" value="СЕКРЕТ_ЗАГЛУШКА" placeholder="Пароль">
  <button id="loginBtn">Войти</button>
</div>
"""

DESKTOP_HTML = """
<main><div id="funcPanel">Продажи</div><section>Начальная страница</section></main>
"""


class CountingPage:
    """Обёртка над страницей: считает клики и fill (зонд не должен их вызывать)."""

    def __init__(self, page) -> None:
        self._page = page
        self.clicks = 0
        self.fills = 0

    def __getattr__(self, name):
        return getattr(self._page, name)

    def click(self, *args, **kwargs):
        self.clicks += 1
        raise AssertionError('зонд не должен кликать по форме')

    def fill(self, *args, **kwargs):
        self.fills += 1
        raise AssertionError('зонд не должен заполнять поля')


@pytest.fixture(scope='module')
def page():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as error:  # pragma: no cover - окружение без Chromium
            pytest.skip(f'Chromium недоступен: {error}')
        context = browser.new_context()
        target = context.new_page()
        try:
            yield target
        finally:
            context.close()
            browser.close()


def test_form_fields_are_collected(page):
    page.set_content(FORM_HTML)
    counted = CountingPage(page)

    facts = collect_login_facts(counted)

    assert facts['login_form_visible'] is True
    assert {'tag': 'input', 'type': 'text', 'id': 'userField', 'name': 'userName'} in facts['fields']
    assert {'tag': 'input', 'type': 'password', 'id': 'pwdField', 'name': ''} in facts['fields']
    assert {'tag': 'button', 'id': 'loginBtn'} in facts['buttons']
    assert facts['containers'] == ['authWindow']
    assert counted.clicks == 0 and counted.fills == 0


def test_page_without_form_reports_false(page):
    page.set_content(DESKTOP_HTML)
    counted = CountingPage(page)

    facts = collect_login_facts(counted)

    assert facts['login_form_visible'] is False
    assert facts['fields'] == [] and facts['buttons'] == [] and facts['containers'] == []
    assert counted.clicks == 0 and counted.fills == 0


def test_facts_never_include_values_or_placeholders(page):
    page.set_content(FORM_HTML)

    facts = collect_login_facts(CountingPage(page))
    serialized = json.dumps(facts, ensure_ascii=False)

    assert 'СЕКРЕТ_ЗАГЛУШКА' not in serialized
    assert 'ТайныйЛогин' not in serialized
    assert 'Пользователь' not in serialized
    assert 'Пароль' not in serialized
    assert 'value' not in serialized
    assert 'placeholder' not in serialized


def test_origin_and_title_are_reported():
    class FakePage:
        url = 'http://1c.example.test/DemoTrd/app?x=1'

        def title(self):
            return '1С:Предприятие'

        def evaluate(self, script):
            return {'inputs': [], 'buttons': [], 'containers': [], 'loginFormVisible': False}

    facts = collect_login_facts(FakePage())

    assert facts['url_origin'] == 'http://1c.example.test'
    assert facts['title'] == '1С:Предприятие'
    assert 'app' not in facts['url_origin']


class FakeSession:
    def __init__(self, project) -> None:
        self.project = project

    def get(self, model, key):  # noqa: ANN001
        return self.project if self.project and self.project.id == key else None


def _run_probe_context(data, project):
    from testpilot.ai.api import _probe_login_context

    return _probe_login_context(data, FakeSession(project))


def test_unknown_project_returns_404():
    with pytest.raises(HTTPException) as error:
        _run_probe_context({'project_id': 'missing'}, None)

    assert error.value.status_code == 404
    assert error.value.detail == 'Проект не найден'


def test_empty_web_url_returns_422():
    project = SimpleNamespace(id='p1', web_url='')

    with pytest.raises(HTTPException) as error:
        _run_probe_context({'project_id': 'p1'}, project)

    assert error.value.status_code == 422
    assert 'web_url' in error.value.detail


def test_url_from_body_is_ignored():
    project = SimpleNamespace(id='p1', web_url='http://project.example.test/DemoTrd?keep=1')

    _, web_url, origin = _run_probe_context(
        {'project_id': 'p1', 'web_url': 'http://evil.example.test/', 'url': 'http://evil.example.test/'}, project)

    assert web_url == 'http://project.example.test/DemoTrd?keep=1'
    assert origin == 'http://project.example.test'
    assert 'evil.example.test' not in web_url + origin
