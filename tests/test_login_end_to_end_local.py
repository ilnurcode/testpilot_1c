"""End-to-end вход и навигация на локальной имитации публикации 1С.

Сеть — только 127.0.0.1: сервер поднимает тест, к реальной 1С обращений нет.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from testpilot.integration.probe import Profile
from testpilot.integration.webclient import WebClientRunner, assert_section

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>1С:Предприятие</title></head>
<body>
  <div id="authWindow">
    <input id="authWindow_basic_login" name="login" type="text">
    <input id="authWindow_basic_password" name="password" type="password">
    <button id="authWindow_basic_okButton" onclick="
      document.getElementById('authWindow').style.display = 'none';
      document.getElementById('desk').hidden = false;">Войти</button>
  </div>
  <main id="desk" hidden>
    <div id="funcPanel">Продажи</div>
    <a href="/sales" id="salesLink">Продажи</a>
  </main>
</body></html>"""

STUCK_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>1С:Предприятие</title></head>
<body>
  <div id="authWindow">
    <input id="authWindow_basic_login" name="login" type="text">
    <input id="authWindow_basic_password" name="password" type="password">
    <button id="authWindow_basic_okButton" onclick="">Войти</button>
  </div>
</body></html>"""


@pytest.fixture(scope='module')
def publication():
    """Локальная публикация: HTML меняется тестом, порт выдаёт ОС."""
    state = {'body': LOGIN_PAGE}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = state['body'].encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # без шума в выводе pytest
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(scope='module')
def browser_page():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as error:  # pragma: no cover - окружение без Chromium
            pytest.skip(f'Chromium недоступен: {error}')
        context = browser.new_context()
        page = context.new_page()
        try:
            page.set_default_timeout(20000)
            yield page
        finally:
            context.close()
            browser.close()


def _runner(port: int) -> WebClientRunner:
    profile = Profile(
        odata_url=f'http://127.0.0.1:{port}/odata',
        web_url=f'http://127.0.0.1:{port}/',
        username='Тест',
        password='',
    )
    return WebClientRunner(profile)


def test_login_then_navigation_passes(publication, browser_page):
    state, port = publication
    state['body'] = LOGIN_PAGE
    runner = _runner(port)

    browser_page.goto(f'http://127.0.0.1:{port}/')
    runner.handle_login(browser_page)
    marker = assert_section(browser_page, 'Продажи')

    assert marker, 'раздел «Продажи» должен быть найден после входа'
    assert browser_page.locator('#authWindow').is_visible() is False, 'форма входа должна исчезнуть'
    assert 'Продажи' in browser_page.inner_text('body')


def test_login_without_navigation_fails_fast(publication, browser_page):
    state, port = publication
    state['body'] = STUCK_LOGIN_PAGE
    runner = _runner(port)
    runner.LOGIN_GRACE_SECONDS = 2

    browser_page.goto(f'http://127.0.0.1:{port}/')
    started = time.monotonic()
    with pytest.raises(RuntimeError) as error:
        runner.handle_login(browser_page)
    elapsed = time.monotonic() - started

    message = str(error.value)
    assert 'Вход в 1С не выполнен' in message
    assert elapsed < 15, f'ошибка должна появляться без зависания, прошло {elapsed:.1f} с'
    assert browser_page.locator('#authWindow').is_visible() is True
