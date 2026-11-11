"""Тесты раннера веб-клиента 1С."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


def test_webclient_runner_real_playwright_execution():
    html = """<!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>1С:Предприятие</title></head>
    <body>
      <button onclick="document.getElementById('status').innerText='Документ успешно проведён'">Провести и закрыть</button>
      <label for="clientInput">Имя клиента</label>
      <input name="Клиент" id="clientInput" />
      <div id="status">Список документов</div>
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
