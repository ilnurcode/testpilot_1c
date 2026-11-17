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


def test_no_form_anywhere_reports_frame_diagnostics(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: url)
    top = FakeFrame('http://project.test/DemoTrd/app', inputs=[{'tag': 'input', 'type': 'text', 'id': 'search', 'name': ''}])
    inner = FakeFrame('http://project.test/DemoTrd/frame', name='content',
                      inputs=[{'tag': 'input', 'type': 'hidden', 'id': 'token', 'name': 'csrf'}])
    page = FramePage('http://project.test/DemoTrd/app', [top, inner])

    with pytest.raises(RuntimeError) as error:
        webclient.probe_login_page(page, 'http://project.test/DemoTrd', timeout_ms=0)

    message = str(error.value)
    assert message.startswith('Не удалось открыть веб-клиент проекта: форма входа не найдена')
    assert 'фреймов: 2' in message
    assert '#0 http://project.test/DemoTrd/app inputs=' in message
    assert '#1 http://project.test/DemoTrd/frame inputs=' in message
    assert "'id': 'search'" in message


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


class FakeRequest:
    def __init__(self, url, resource_type='document', method='GET'):
        self.url = url
        self.resource_type = resource_type
        self.method = method


class FakeRoute:
    def __init__(self, request):
        self.request = request
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


class RoutingPage(FakeProbePage):
    """Страница с перехватом маршрутов: позволяет стрелять guard-ом без сети."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.route_handler = None

    def route(self, selector, handler):
        self.route_handler = handler

    def fire(self, url, resource_type='document', method='GET'):
        route = FakeRoute(FakeRequest(url, resource_type, method))
        self.route_handler(route)
        return route


def test_route_guard_blocks_foreign_and_allows_own_origin():
    from testpilot.integration.webclient import _origin, probe_route_guard

    allowed = {_origin('http://project.test/DemoTrd')}
    state = {'blocked': 0, 'blocked_non_get': 0, 'blocked_origins': [], 'blocked_main': False}
    guard = probe_route_guard(allowed, state)

    own = FakeRoute(FakeRequest('http://project.test/DemoTrd/app.js', 'script'))
    guard(own)
    foreign = FakeRoute(FakeRequest('http://evil.example.test/track.js', 'script'))
    guard(foreign)

    assert own.continued is True and own.aborted is False
    assert foreign.aborted is True and foreign.continued is False
    assert state['blocked'] == 1
    assert state['blocked_origins'] == ['http://evil.example.test']


def test_blocked_main_navigation_is_503(monkeypatch):
    from testpilot.integration import webclient

    page = RoutingPage(url='about:blank')

    def blocked_navigate(page_obj, url, **kwargs):
        page_obj.fire('http://evil.example.test/redirect')
        raise RuntimeError('net::ERR_FAILED')

    monkeypatch.setattr(webclient, 'navigate', blocked_navigate)

    with pytest.raises(RuntimeError) as error:
        webclient.probe_login_page(page, 'http://project.test/DemoTrd')

    assert str(error.value) == 'Запрос на сторонний origin отклонён: http://evil.example.test (разрешено: http://project.test)'


def test_successful_navigation_blocks_nothing(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: url)
    page = RoutingPage(url='http://project.test/DemoTrd/app')

    result = webclient.probe_login_page(page, 'http://project.test/DemoTrd')

    assert result['blocked_requests'] == 0


def test_blocked_subresource_does_not_fail_navigation(monkeypatch):
    from testpilot.integration import webclient

    def navigate_and_track(page_obj, url, **kwargs):
        page_obj.fire('http://project.test/DemoTrd/app')
        page_obj.fire('http://cdn.thirdparty.test/analytics.js', 'script')
        return url

    monkeypatch.setattr(webclient, 'navigate', navigate_and_track)
    page = RoutingPage(url='http://project.test/DemoTrd/app')

    result = webclient.probe_login_page(page, 'http://project.test/DemoTrd')

    assert result['blocked_requests'] == 1
    assert result['login_form_visible'] is False


def test_same_origin_post_is_blocked():
    from testpilot.integration.webclient import _origin, probe_route_guard

    allowed = {_origin('http://project.test/DemoTrd')}
    state = {'blocked': 0, 'blocked_non_get': 0, 'blocked_origins': [], 'blocked_main': False}
    guard = probe_route_guard(allowed, state)

    post = FakeRoute(FakeRequest('http://project.test/DemoTrd/api/save', 'xhr', 'POST'))
    guard(post)

    assert post.aborted is True and post.continued is False
    assert state['blocked_non_get'] == 1
    assert state['blocked'] == 0


def test_same_origin_get_is_allowed():
    from testpilot.integration.webclient import _origin, probe_route_guard

    allowed = {_origin('http://project.test/DemoTrd')}
    state = {'blocked': 0, 'blocked_non_get': 0, 'blocked_origins': [], 'blocked_main': False}
    guard = probe_route_guard(allowed, state)

    get = FakeRoute(FakeRequest('http://project.test/DemoTrd/static/app.js', 'script', 'GET'))
    head = FakeRoute(FakeRequest('http://project.test/DemoTrd/', 'document', 'HEAD'))
    guard(get)
    guard(head)

    assert get.continued is True and get.aborted is False
    assert head.continued is True and head.aborted is False
    assert state['blocked_non_get'] == 0


def test_foreign_get_is_blocked():
    from testpilot.integration.webclient import _origin, probe_route_guard

    allowed = {_origin('http://project.test/DemoTrd')}
    state = {'blocked': 0, 'blocked_non_get': 0, 'blocked_origins': [], 'blocked_main': False}
    guard = probe_route_guard(allowed, state)

    foreign = FakeRoute(FakeRequest('http://evil.example.test/pixel.gif', 'image', 'GET'))
    guard(foreign)

    assert foreign.aborted is True and foreign.continued is False
    assert state['blocked'] == 1


def test_successful_probe_reports_zero_non_get(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: url)
    page = RoutingPage(url='http://project.test/DemoTrd/app')

    result = webclient.probe_login_page(page, 'http://project.test/DemoTrd')

    assert result['blocked_non_get'] == 0
    assert result['blocked_requests'] == 0


def _patch_targets(monkeypatch, *urls):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'container_access_urls', lambda web_url: list(urls))


def test_probe_falls_back_to_second_address(monkeypatch):
    from testpilot.integration import webclient

    first, second = 'http://localhost:8088/DemoTrd', 'http://host.docker.internal:8088/DemoTrd'
    _patch_targets(monkeypatch, first, second)
    page = RoutingPage(url='about:blank')
    attempts = []

    def navigate(page_obj, url, **kwargs):
        attempts.append(url)
        if url == first:
            raise RuntimeError('page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:8088/DemoTrd')
        page_obj._url = url + '/'

    monkeypatch.setattr(webclient, 'navigate', navigate)

    result = webclient.probe_login_page(page, first)

    assert attempts == [first, second]
    assert result['web_url_used'] == 'http://host.docker.internal:8088'


def test_probe_lists_all_addresses_when_everything_refuses(monkeypatch):
    from testpilot.integration import webclient

    first, second = 'http://localhost:8088/DemoTrd', 'http://host.docker.internal:8088/DemoTrd'
    _patch_targets(monkeypatch, first, second)
    page = RoutingPage(url='about:blank')

    def navigate(page_obj, url, **kwargs):
        raise RuntimeError(f'page.goto: net::ERR_CONNECTION_REFUSED at {url}')

    monkeypatch.setattr(webclient, 'navigate', navigate)

    with pytest.raises(RuntimeError) as error:
        webclient.probe_login_page(page, first)

    message = str(error.value)
    assert message.startswith('Не удалось открыть веб-клиент проекта:')
    assert 'испробованы: http://localhost:8088, http://host.docker.internal:8088' in message


def test_probe_does_not_retry_on_timeout(monkeypatch):
    from testpilot.integration import webclient

    first, second = 'http://localhost:8088/DemoTrd', 'http://host.docker.internal:8088/DemoTrd'
    _patch_targets(monkeypatch, first, second)
    page = RoutingPage(url='about:blank')
    attempts = []

    def navigate(page_obj, url, **kwargs):
        attempts.append(url)
        raise RuntimeError('page.goto: Timeout 20000ms exceeded')

    monkeypatch.setattr(webclient, 'navigate', navigate)

    with pytest.raises(RuntimeError) as error:
        webclient.probe_login_page(page, first)

    assert attempts == [first]
    assert 'Timeout 20000ms exceeded' in str(error.value)
    assert 'испробованы' not in str(error.value)


class FakeFrame:
    """Фрейм-мок: evaluate отвечает на известные скрипты зонда, клики и fill запрещены."""

    def __init__(self, url, name='', ready=False, facts=None, inputs=None):
        self.url = url
        self.name = name
        self.ready = ready
        self.facts = facts or {'inputs': [], 'buttons': [], 'containers': [], 'loginFormVisible': False}
        self.inputs = inputs or []

    def evaluate(self, script):
        from testpilot.integration import webclient

        if script == webclient.PROBE_READY_SCRIPT:
            return self.ready
        if script == webclient.LOGIN_PROBE_SCRIPT:
            return self.facts
        if script == webclient.FRAME_DIAGNOSTIC_SCRIPT:
            return self.inputs
        return None

    def click(self, *args, **kwargs):
        raise AssertionError('зонд не должен кликать')

    def fill(self, *args, **kwargs):
        raise AssertionError('зонд не должен заполнять поля')


class FramePage:
    def __init__(self, url, frames):
        self._url = url
        self._frames = frames
        self.route_handler = None

    @property
    def url(self):
        return self._url

    @property
    def frames(self):
        return list(self._frames)

    def title(self):
        return '1С:Предприятие'

    def route(self, selector, handler):
        self.route_handler = handler

    def wait_for_timeout(self, ms):
        return None

    def click(self, *args, **kwargs):
        raise AssertionError('зонд не должен кликать')

    def fill(self, *args, **kwargs):
        raise AssertionError('зонд не должен заполнять поля')


def test_form_inside_iframe_is_found(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: url)
    facts = {'inputs': [{'tag': 'input', 'type': 'text', 'id': 'authWindow_basic_login', 'name': 'user'}],
             'buttons': [{'tag': 'button', 'id': 'ok'}],
             'containers': ['authWindow'], 'loginFormVisible': True}
    top = FakeFrame('http://project.test/DemoTrd/app')
    inner = FakeFrame('http://project.test/DemoTrd/login?tenant=1', name='auth', ready=True, facts=facts)
    page = FramePage('http://project.test/DemoTrd/app', [top, inner])

    result = webclient.probe_login_page(page, 'http://project.test/DemoTrd', timeout_ms=0)

    assert result['login_form_visible'] is True
    assert result['fields'] == facts['inputs']
    assert result['frame_url'] == 'http://project.test/DemoTrd/login'
    assert result['frame_name'] == 'auth'


def test_form_in_top_document_still_works(monkeypatch):
    from testpilot.integration import webclient

    monkeypatch.setattr(webclient, 'navigate', lambda page, url, **kwargs: url)
    facts = {'inputs': [{'tag': 'input', 'type': 'password', 'id': 'pwd', 'name': ''}],
             'buttons': [], 'containers': [], 'loginFormVisible': True}
    top = FakeFrame('http://project.test/DemoTrd/app', ready=True, facts=facts)
    page = FramePage('http://project.test/DemoTrd/app', [top])

    result = webclient.probe_login_page(page, 'http://project.test/DemoTrd', timeout_ms=0)

    assert result['login_form_visible'] is True
    assert result['frame_url'] == 'http://project.test/DemoTrd/app'
    assert result['frame_name'] == ''
