from types import SimpleNamespace

from testpilot.integration.probe import Profile
from testpilot.integration.webclient import WebClientRunner


def _mock_runner(monkeypatch):
    import testpilot.integration.webclient as webclient

    navigations = []
    logins = []

    class Page:
        def set_default_timeout(self, timeout):
            pass

        def goto(self, url, **kwargs):
            navigations.append(url)

        def locator(self, selector):
            return SimpleNamespace(inner_text=lambda: '')

        def close(self):
            pass

    page = Page()

    class Context:
        def new_page(self):
            return page

    context = Context()

    class Browser:
        def new_context(self, **kwargs):
            return context

        def close(self):
            pass

    browser = Browser()
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: browser))
    monkeypatch.setattr(webclient, 'browser_path', lambda _: 'fake-chromium')
    monkeypatch.setattr(webclient, '_safe_screenshot', lambda *_: b'png')
    runner = WebClientRunner(Profile(
        odata_url='https://trusted.example/odata',
        web_url='https://trusted.example/DemoTrd',
        username='admin',
        password='secret',
    ))
    monkeypatch.setattr(runner, 'handle_login', lambda _: logins.append('login'))
    monkeypatch.setattr(runner, 'wait_for_1c', lambda _: None)
    return runner, playwright, navigations, logins


def test_open_url_rejects_external_origin_before_navigation_or_login(monkeypatch):
    runner, playwright, navigations, logins = _mock_runner(monkeypatch)
    report = runner.run_scenario('Внешняя навигация', [
        {'action': 'open_url', 'target': 'https://evil.example/'},
    ], playwright_instance=playwright)
    assert report.status == 'failed'
    assert report.steps[-1].message == 'Отказано в open_url: origin целевого URL не совпадает с origin веб-клиента проекта'
    assert navigations == ['https://trusted.example/DemoTrd']
    assert logins == ['login']


def test_open_url_allows_configured_origin(monkeypatch):
    runner, playwright, navigations, logins = _mock_runner(monkeypatch)
    report = runner.run_scenario('Внутренняя навигация', [
        {'action': 'open_url', 'target': 'https://trusted.example/another-path'},
    ], playwright_instance=playwright)
    assert report.status == 'passed', report.error
    assert navigations == ['https://trusted.example/DemoTrd', 'https://trusted.example/another-path']
    assert logins == ['login', 'login']
