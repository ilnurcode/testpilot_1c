"""Ошибка входа: run_scenario сохраняет скриншот до закрытия страницы.

Отдельный модуль: модульный фикстур-браузер других тестов держит sync-Playwright
и не даёт запустить второй экземпляр в том же потоке.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from testpilot.integration.probe import Profile
from testpilot.integration.webclient import WebClientRunner

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
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = STUCK_LOGIN_PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_run_scenario_keeps_screenshot_when_login_fails(publication):
    port = publication
    profile = Profile(
        odata_url=f'http://127.0.0.1:{port}/odata',
        web_url=f'http://127.0.0.1:{port}/',
        username='Тест',
        password='',
    )
    runner = WebClientRunner(profile)
    runner.LOGIN_GRACE_SECONDS = 2

    report = runner.run_scenario('Вход и навигация', [])

    assert report.status == 'error'
    assert 'Вход в 1С не выполнен' in (report.error or '')
    assert report.screenshots, 'скриншот ошибки входа должен сохраняться'
    assert report.screenshots[0][:8] == b'\x89PNG\r\n\x1a\n'
    assert runner.last_login_diagnostics is not None
    assert runner.last_login_diagnostics['clicked'] is True
    assert runner.last_login_diagnostics['form_visible_after'] is True
    assert report.pre_submit_screenshot[:8] == b'\x89PNG\r\n\x1a\n', 'есть fallback-снимок до клика'
