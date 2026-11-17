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


class FakeProbePage:
    """Мок страницы для зонда: url/wait/evaluate без браузера и сети."""

    def __init__(self, url='http://project.test/DemoTrd/app', facts=None):
        self._url = url
        self.facts = facts or {'inputs': [], 'buttons': [], 'containers': [], 'loginFormVisible': False}
        self.waited = 0

    @property
    def url(self):
        return self._url

    def title(self):
        return '1С:Предприятие'

    def evaluate(self, script):
        return self.facts

    def wait_for_function(self, script, timeout=None, arg=None):
        return True

    def wait_for_timeout(self, ms):
        self.waited += 1

    def set_default_timeout(self, ms):
        return None


def test_navigation_error_becomes_explicit_error(monkeypatch):
    from testpilot.integration import webclient

    def failing_navigate(page, url, **kwargs):
        raise RuntimeError('net::ERR_CONNECTION_REFUSED')

    monkeypatch.setattr(webclient, 'navigate', failing_navigate)

    with pytest.raises(RuntimeError) as error:
        webclient.probe_login_page(FakeProbePage(), 'http://project.test/DemoTrd')

    message = str(error.value)
    assert message == 'Не удалось открыть веб-клиент проекта: net::ERR_CONNECTION_REFUSED'
    assert 'login_form_visible' not in message


def test_foreign_origin_is_rejected(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: page.url)
    page = FakeProbePage(url='http://evil.example.test/app')

    with pytest.raises(RuntimeError) as error:
        webclient.probe_login_page(page, 'http://project.test/DemoTrd')

    message = str(error.value)
    assert message == 'Страница ушла на другой origin: http://evil.example.test (ожидался http://project.test)'


def test_about_blank_is_rejected(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: url)
    page = FakeProbePage(url='about:blank')

    with pytest.raises(RuntimeError) as error:
        webclient.probe_login_page(page, 'http://project.test/DemoTrd')

    assert str(error.value).startswith('Не удалось открыть веб-клиент проекта:')
    assert 'about:blank' in str(error.value)


def test_wait_timeout_becomes_explicit_error(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: url)
    page = FakeProbePage(url='http://project.test/DemoTrd/app')

    def timeout(*args, **kwargs):
        raise RuntimeError('Timeout 20000ms exceeded')

    page.wait_for_function = timeout

    with pytest.raises(RuntimeError) as error:
        webclient.probe_login_page(page, 'http://project.test/DemoTrd')

    assert str(error.value).startswith('Не удалось открыть веб-клиент проекта:')
    assert 'Timeout 20000ms exceeded' in str(error.value)


def test_successful_probe_returns_facts(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: url)
    facts = {'inputs': [{'tag': 'input', 'type': 'password', 'id': 'pwd', 'name': ''}],
             'buttons': [{'tag': 'button', 'id': 'ok'}], 'containers': ['authWindow'], 'loginFormVisible': True}
    page = FakeProbePage(url='http://project.test/DemoTrd/app', facts=facts)

    result = webclient.probe_login_page(page, 'http://project.test/DemoTrd')

    assert result['login_form_visible'] is True
    assert result['fields'] == facts['inputs']
    assert result['buttons'] == facts['buttons']
    assert result['containers'] == ['authWindow']
    assert result['url_origin'] == 'http://project.test'


def test_probe_errors_map_to_503(monkeypatch):
    from testpilot.ai.api import _probe_login_facts

    def failing_probe(web_url):
        raise RuntimeError('Не удалось открыть веб-клиент проекта: net::ERR_NAME_NOT_RESOLVED')

    monkeypatch.setattr('testpilot.integration.webclient.probe_login_form', failing_probe)

    with pytest.raises(HTTPException) as error:
        _probe_login_facts('http://project.test/DemoTrd')

    assert error.value.status_code == 503
    assert error.value.detail == 'Не удалось открыть веб-клиент проекта: net::ERR_NAME_NOT_RESOLVED'
