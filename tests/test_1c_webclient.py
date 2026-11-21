"""Тесты раннера веб-клиента 1С."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import httpx
import testpilot.integration.probe as probe
from testpilot.integration.odata import ODataClient
import pytest
from testpilot.integration.probe import Profile
from testpilot.integration.webclient import WebClientRunner, StepResult, ScenarioReport

PROFILE = Profile(odata_url='https://1c.example.test/trade', web_url='https://1c.example.test/trade_web', username='admin', password='123')

def test_webclient_runner_initialization():
    runner = WebClientRunner(PROFILE)
    assert runner.web_url == 'https://1c.example.test/trade_web'
    assert runner.headless is True

def test_scenario_report_data_structures():
    step = StepResult(
        step_number=1,
        action='click',
        target='Записать',
        value=None,
        status='passed',
        message='OK',
        duration_seconds=0.12,
        screenshot=b'fake-png'
    )
    assert step.action == 'click'
    assert step.status == 'passed'

    report = ScenarioReport(title='Тест проведения', status='passed', steps=[step])
    assert len(report.steps) == 1
    assert report.status == 'passed'


def test_webclient_runner_real_playwright_execution(monkeypatch):
    monkeypatch.setattr(probe, 'is_containerized', lambda: False)
    html = """<!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>1С:Предприятие</title></head>
    <body>
      <button onclick="document.getElementById('status').innerText='Документ успешно проведён'">Провести и закрыть</button>
      <label for="clientInput">Имя клиента</label>
      <input name="Клиент" id="clientInput" />
      <div id="status">Список документов</div>
      <div id="funcPanel">Главное</div>
    </body></html>
    """.encode("utf-8")

    class MockHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)
        def log_message(self, format, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        local_profile = Profile(
            odata_url=f"http://127.0.0.1:{port}/odata",
            web_url=f"http://127.0.0.1:{port}/web",
        )
        runner = WebClientRunner(local_profile, headless=True)
        steps = [
            {"action": "fill", "target": "Имя клиента", "value": "ООО Тест"},
            {"action": "click", "target": "Провести и закрыть"},
            {"action": "assert_text", "target": "Документ успешно проведён"},
        ]
        report = runner.run_scenario("Создание и проведение документа в 1С", steps)
        assert report.status == "passed", report.error
        assert len(report.steps) == 4  # open_url + 3 steps
        assert len(report.screenshots) == 4
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def visible_text_runner(monkeypatch):
    html = """<!DOCTYPE html>
    <html><head><meta charset="utf-8"></head><body>
      <button style="display:none" onclick="document.getElementById('result').textContent='Нажата скрытая'">Продажи</button>
      <button onclick="document.getElementById('result').textContent='Нажата видимая'">Продажи</button>
      <button style="display:none">Скрытая кнопка</button>
      <div>Только часть текста</div>
      <button onclick="setTimeout(() => document.getElementById('later').style.display='block', 300)">Показать позже</button>
      <div id="later" style="display:none">Появившийся текст</div>
      <div id="result">Ожидание</div>
    </body></html>""".encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(probe, 'is_containerized', lambda: False)
    monkeypatch.setattr(WebClientRunner, "handle_login", lambda self, page: None)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        yield WebClientRunner(Profile(odata_url=url, web_url=url), default_timeout=500)
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


def test_visible_exact_text_assert_and_click(visible_text_runner):
    report = visible_text_runner.run_scenario("Видимое совпадение", [
        {"action": "assert_text", "target": "Продажи"},
        {"action": "click", "target": "Продажи"},
        {"action": "assert_text", "target": "Нажата видимая"},
        {"action": "click", "target": "Показать позже"},
        {"action": "assert_text", "target": "Появившийся текст"},
    ])
    assert report.status == "passed", report.error
    assert [step.status for step in report.steps] == ["passed"] * 6


@pytest.mark.parametrize("target", ["Скрытая кнопка", "Только часть"])
def test_assert_text_requires_visible_exact_match(visible_text_runner, target):
    report = visible_text_runner.run_scenario("Нет видимого точного текста", [
        {"action": "assert_text", "target": target},
    ])
    assert report.status == "failed"
    assert report.steps[-1].message == f"Текст '{target}' не обнаружен на форме 1С"


def test_click_does_not_use_hidden_text(visible_text_runner):
    report = visible_text_runner.run_scenario("Скрытая кнопка", [
        {"action": "click", "target": "Скрытая кнопка"},
    ])
    assert report.status == "failed"
    assert report.steps[-1].action == "click"
    assert report.steps[-1].status == "failed"


def test_assert_section_requires_active_marker_from_mock_html(monkeypatch):
    pages = {
        '/active': '<html><body><nav><button class="themeBox select">Продажи</button></nav><main><h1>Информация</h1></main></body></html>',
        '/idle': '<html><body><nav><button>Продажи</button></nav><main><h1>Информация</h1></main></body></html>',
        '/content': "<html><body><nav><button onclick=\"document.getElementById('workspace').textContent='Оптовые продажи'\">Продажи</button></nav><main id='workspace'>Начальная страница</main></body></html>",
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path, '').encode('utf-8')
            self.send_response(200 if body else 404)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    srv = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(probe, 'is_containerized', lambda: False)
    monkeypatch.setattr(WebClientRunner, 'handle_login', lambda self, page: None)
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    try:
        active = WebClientRunner(Profile(odata_url=base + '/active', web_url=base + '/active'), default_timeout=100)
        passed = active.run_scenario('Активный раздел', [{'action': 'assert_section', 'target': 'Продажи'}])
        assert passed.status == 'passed', passed.error
        assert 'классом select выбранного пункта навигации' in passed.steps[-1].message

        idle = WebClientRunner(Profile(odata_url=base + '/idle', web_url=base + '/idle'), default_timeout=50)
        failed = idle.run_scenario('Только пункт меню', [{'action': 'assert_section', 'target': 'Продажи'}])
        assert failed.status == 'failed'
        assert failed.steps[-1].message == 'Раздел «Продажи» не подтверждён: активный маркер или новая строка рабочей области не найдены'

        content = WebClientRunner(Profile(odata_url=base + '/content', web_url=base + '/content'), default_timeout=2000)
        changed = content.run_scenario('Новая строка рабочей области', [
            {'action': 'click', 'target': 'Продажи'},
            {'action': 'assert_section', 'target': 'Продажи'},
        ])
        assert changed.status == 'passed', changed.error
        assert 'новой строкой рабочей области «Оптовые продажи»' in changed.steps[-1].message
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


def test_container_loopback_routing_for_web_and_odata(monkeypatch):
    monkeypatch.setattr(probe, 'is_containerized', lambda: True)
    profile = Profile(
        odata_url='http://localhost:8088/DemoTrd/odata',
        web_url='http://127.0.0.1:8088/DemoTrd',
    )
    runner = WebClientRunner(profile)
    assert runner.web_url == 'http://host.docker.internal:8088/DemoTrd'

    requested = []

    def handler(request):
        requested.append(request.url)
        return httpx.Response(200, json={'value': []})

    assert ODataClient(profile, transport=httpx.MockTransport(handler)).get_entities('Catalog_Test') == []
    assert requested[-1].host == 'host.docker.internal'
    assert requested[-1].port == 8088
    assert requested[-1].path == '/DemoTrd/odata/Catalog_Test'

    explicit = Profile(odata_url='http://1c.example.test:8088/odata', web_url='http://1c.example.test:8088/web')
    assert WebClientRunner(explicit).web_url == 'http://1c.example.test:8088/web'

    monkeypatch.setattr(probe, 'is_containerized', lambda: False)
    outside = Profile(odata_url='http://localhost:8088/odata', web_url='http://127.0.0.1:8088/web')
    assert WebClientRunner(outside).web_url == 'http://127.0.0.1:8088/web'
    assert ODataClient(outside, transport=httpx.MockTransport(handler)).get_entities('Catalog_Test') == []
    assert requested[-1].host == 'localhost'
    assert requested[-1].port == 8088




class _FakePage:
    """Мок страницы: goto отдаёт заранее заданную реакцию на адрес и запоминает вызовы."""

    def __init__(self, react):
        self.react = react
        self.visits = []

    def goto(self, url, **kwargs):
        self.visits.append(url)
        outcome = self.react(url)
        if isinstance(outcome, BaseException):
            raise outcome
        return None


def _nav_error(message):
    return RuntimeError(message)


def _timeout_error(message):
    return type('TimeoutError', (Exception,), {})(message)


def test_navigation_falls_back_to_original_address_on_connection_refused(monkeypatch):
    import testpilot.integration.probe as probe_module
    from testpilot.integration.webclient import navigate

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    page = _FakePage(lambda url: _nav_error('page.goto: net::ERR_CONNECTION_REFUSED at ' + url)
                     if 'host.docker.internal' in url else None)

    used = navigate(page, 'http://127.0.0.1:8098')

    assert used == 'http://127.0.0.1:8098'
    assert page.visits == ['http://host.docker.internal:8098', 'http://127.0.0.1:8098']


def test_navigation_uses_mapped_address_without_retry(monkeypatch):
    import testpilot.integration.probe as probe_module
    from testpilot.integration.webclient import navigate

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    page = _FakePage(lambda url: None)

    used = navigate(page, 'http://127.0.0.1:8098')

    assert used == 'http://host.docker.internal:8098'
    assert page.visits == ['http://host.docker.internal:8098']


@pytest.mark.parametrize('failure', [
    _nav_error('page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE 401'),
    _nav_error('Ошибка на форме входа: поле не найдено'),
])
def test_navigation_does_not_retry_non_network_errors(monkeypatch, failure):
    import testpilot.integration.probe as probe_module
    from testpilot.integration.webclient import navigate

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    page = _FakePage(lambda url: failure)

    with pytest.raises(RuntimeError):
        navigate(page, 'http://127.0.0.1:8098')
    assert page.visits == ['http://host.docker.internal:8098']


def test_navigation_document_load_timeout_does_not_fall_back(monkeypatch):
    import testpilot.integration.probe as probe_module
    from testpilot.integration.webclient import navigate

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    page = _FakePage(lambda url: _timeout_error('page.goto: Timeout 45000ms exceeded.'))

    with pytest.raises(Exception) as failure:
        navigate(page, 'http://127.0.0.1:8098')

    assert 'Timeout 45000ms exceeded' in str(failure.value)
    assert 'откат' not in str(failure.value).lower()
    assert page.visits == ['http://host.docker.internal:8098']


def test_navigation_explicit_host_has_single_address(monkeypatch):
    import testpilot.integration.probe as probe_module
    from testpilot.integration.webclient import navigate

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    page = _FakePage(lambda url: _nav_error('net::ERR_CONNECTION_REFUSED'))

    with pytest.raises(RuntimeError):
        navigate(page, 'http://1c.example.test:8088/DemoTrd')
    assert page.visits == ['http://1c.example.test:8088/DemoTrd']


def test_runner_reports_address_that_answered_after_fallback(monkeypatch):
    import testpilot.integration.probe as probe_module
    from testpilot.integration.webclient import WebClientRunner

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    runner = WebClientRunner(Profile(odata_url='http://127.0.0.1:8098/odata', web_url='http://127.0.0.1:8098/web'))
    page = _FakePage(lambda url: _nav_error('net::ERR_CONNECTION_REFUSED')
                     if 'host.docker.internal' in url else None)
    assert runner.web_url == 'http://host.docker.internal:8098/web'
    assert runner.last_web_url == 'http://host.docker.internal:8098/web'

    from testpilot.integration.webclient import navigate

    assert runner.web_urls == ['http://host.docker.internal:8098/web', 'http://127.0.0.1:8098/web']
    assert runner.raw_web_url == 'http://127.0.0.1:8098/web'
    assert navigate(page, runner.raw_web_url) == 'http://127.0.0.1:8098/web'
    assert page.visits == ['http://host.docker.internal:8098/web', 'http://127.0.0.1:8098/web']


def test_navigation_retries_same_address_after_chrome_error_race(monkeypatch):
    import testpilot.integration.probe as probe_module
    from testpilot.integration.webclient import navigate

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    state = {'raced': False}

    def react(url):
        if 'host.docker.internal' in url and not state['raced']:
            state['raced'] = True
            return _nav_error(
                'Page.goto: Navigation to "http://127.0.0.1:8098/" is interrupted by '
                'another navigation to "chrome-error://chromewebdata/"'
            )
        return None

    page = _FakePage(react)
    assert navigate(page, 'http://127.0.0.1:8098') == 'http://host.docker.internal:8098'
    assert page.visits == ['http://host.docker.internal:8098', 'http://host.docker.internal:8098']


def test_negative_section_assertion_requires_live_workspace(monkeypatch):
    """B3: отсутствие раздела на странице 401 — сбой прогона, а не подтверждённое «раздела нет»."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            denied = self.path.startswith('/deny')
            body = ('<html><body><h1>Требуется авторизация</h1></body></html>' if denied else
                    "<html><body><div id='funcPanel'>Начальная страница</div></body></html>")
            self.send_response(401 if denied else 200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode('utf-8'))

        def log_message(self, format, *args):
            pass

    srv = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(probe, 'is_containerized', lambda: False)
    monkeypatch.setattr(WebClientRunner, 'handle_login', lambda self, page: None)
    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    assertion = {'id': 'neg', 'scope': 'connectivity', 'check': 'web_section_visible',
                 'engine': 'playwright', 'operator': 'exists', 'selector': {'section': 'Продажи'},
                 'expected': {'type': 'boolean', 'value': False}, 'required': True}
    try:
        denied = WebClientRunner(Profile(odata_url=base + '/deny', web_url=base + '/deny'),
                                 default_timeout=100)
        error = denied.run_scenario('Страница 401', [], assertions=[assertion])
        assert error.assertions['neg']['status'] == 'error'
        assert 'рабочая область 1С не открыта' in error.assertions['neg']['error']

        live = WebClientRunner(Profile(odata_url=base + '/workspace', web_url=base + '/workspace'),
                               default_timeout=100)
        confirmed = live.run_scenario('Живая рабочая область', [], assertions=[assertion])
        assert confirmed.assertions['neg']['value'] is False
        assert 'не подтверждён' in confirmed.assertions['neg']['error']
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()
