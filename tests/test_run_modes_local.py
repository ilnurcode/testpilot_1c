"""Режимы прогона на локальной имитации публикации 1С: реальный браузер, без живой 1С.

Сеть — только 127.0.0.1: сервер поднимает тест. Гварды allow_mutations/strict_read_only проверяются
не только по коду ответа, но и по фактам: запрос страницы и POST /saved считает сам сервер.
"""
from __future__ import annotations

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from testpilot.ai import provider
from testpilot.ai.models import Suggestion
from testpilot.impact.algorithms import analyze
from testpilot.web.presentation import sample

DESKTOP_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>1С:Предприятие</title></head>
<body>
  <main>
    <div id="funcPanel">Главное</div>
    <div id="sales" aria-current="page">Продажи</div>
    <button id="save" onclick="fetch('/saved', {method: 'POST'});">Записать</button>
    <button onclick="document.getElementById('sales').textContent='Заказы'">Сменить раздел</button>
  </main>
</body></html>"""


def package():
    return analyze(sample(1), sample(2), {'edge_types': ['calls', 'uses', 'belongs_to', 'subscribes_to'],
                                          'direction': 'impact', 'max_depth': 10})[1]


@pytest.fixture
def publication():
    """Локальная публикация: считает GET страницы, POST /saved и отдаёт их через GET /saved-count."""
    state = {'page_hits': 0, 'saved': 0}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str = 'text/html; charset=utf-8'):
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/saved-count':
                self._send(str(state['saved']).encode('utf-8'), 'text/plain; charset=utf-8')
                return
            state['page_hits'] += 1
            self._send(DESKTOP_PAGE.encode('utf-8'))

        def do_POST(self):
            if self.path == '/saved':
                state['saved'] += 1
                self._send(b'ok', 'text/plain; charset=utf-8')
                return
            self.send_response(404)
            self.end_headers()

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


def _suite_with_steps(clients, monkeypatch, steps: list[dict], port: int, *, section: str = 'Продажи') -> tuple:
    impact, c = clients
    p = package()
    assert c.post('/api/v1/impact-packages', json=p).status_code == 200
    project = impact.post('/api/v1/projects', json={'name': 'Локальная имитация', 'description': ''}).json()
    connection = impact.put(
        f"/api/v1/projects/{project['id']}/connection",
        json={
            'odata_url': f'http://127.0.0.1:{port}/odata/standard.odata',
            'web_url': f'http://127.0.0.1:{port}/',
            'auth_username': None,
            'auth_password': None,
        },
    )
    assert connection.status_code == 200, connection.text

    suggestion = Suggestion.model_validate({
        'summary': 'План на локальной имитации',
        'selected_test_ids': [],
        'proposals': [{
            'title': 'Проверка кнопки Записать',
            'rationale': 'Локальная имитация публикации',
            'engine': 'playwright',
            'object_ids': [p['affected_objects'][0]],
            'steps': ['Проверить поведение кнопки'],
            'expected_result': 'Счётчик POST /saved совпадает',
            'cases': [{'steps': steps, 'assertions': [{'id': 'probe', 'scope': 'connectivity', 'check': 'web_section_visible', 'engine': 'playwright', 'operator': 'exists', 'selector': {'section': section}, 'expected': {'type': 'boolean', 'value': True}}]}],
        }],
        'warnings': [],
    })
    monkeypatch.setattr(provider, 'generate', lambda *a, **kw: (suggestion, 'mock-model'))
    created = c.post('/api/v1/ai/suites', json={
        'package_id': p['package_id'],
        'project_id': project['id'],
        'provider': 'llm',
        'consent': True,
    })
    assert created.status_code == 200, created.text
    suite = created.json()
    approved = c.post(
        f"/api/v1/ai/suites/{suite['suite_id']}/approve",
        json={'reason': 'Изменяющие шаги на локальной имитации', 'selected_proposals': [0]},
    )
    assert approved.status_code == 200, approved.text
    return c, suite


def _saved(port: int) -> int:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/saved-count', timeout=5) as response:
        return int(response.read().decode('utf-8'))


def _run_local(c, sid, params=None):
    import time
    response = c.post(f"/api/v1/ai/suites/{sid}/run-1c", params=params or {})
    if response.status_code not in (200, 202):
        return response
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = c.get(f"/api/v1/ai/suites/{sid}/status").json()
        if state['status'] not in ('queued', 'running'):
            return c.get(f"/api/v1/ai/suites/{sid}")
        time.sleep(0.05)
    raise AssertionError(state)


MUTATING_STEPS = [{'action': 'click', 'target': 'Записать'}, {'action': 'wait', 'value': 500}]


def test_run_without_consent_is_blocked_before_browser(clients, monkeypatch, publication):
    state, port = publication
    c, suite = _suite_with_steps(clients, monkeypatch, MUTATING_STEPS, port)

    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c")

    assert response.status_code == 422, response.text
    assert 'передайте allow_mutations=true' in response.json()['error']['message']
    assert state['page_hits'] == 0, 'браузер не должен запускаться до проверки гварда'
    assert state['saved'] == 0


def test_run_with_allow_mutations_really_clicks(clients, monkeypatch, publication):
    state, port = publication
    c, suite = _suite_with_steps(clients, monkeypatch, MUTATING_STEPS, port)

    response = _run_local(c, suite['suite_id'], params={'allow_mutations': 'true'})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['allow_mutations'] is True
    assert body['status'] == 'completed'
    assert body['results'][0]['status'] == 'passed', body['results'][0]
    assert state['page_hits'] >= 1
    assert state['saved'] == 1
    assert _saved(port) == 1


def test_strict_read_only_blocks_click_before_browser(clients, monkeypatch, publication):
    state, port = publication
    c, suite = _suite_with_steps(clients, monkeypatch, MUTATING_STEPS, port)

    response = _run_local(c, suite['suite_id'], params={'strict_read_only': 'true'})

    assert response.status_code == 422, response.text
    assert 'strict_read_only' in response.json()['error']['message']
    assert state['page_hits'] == 0
    assert state['saved'] == 0


def test_strict_read_only_runs_assert_text_without_posts(clients, monkeypatch, publication):
    state, port = publication
    c, suite = _suite_with_steps(
        clients, monkeypatch,
        [{'action': 'assert_text', 'target': 'Продажи'}, {'action': 'wait', 'value': 300}],
        port,
    )

    response = _run_local(c, suite['suite_id'], params={'strict_read_only': 'true'})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['strict_read_only'] is True
    assert body['allow_mutations'] is False
    assert body['results'][0]['status'] == 'passed', body['results'][0]
    assert state['page_hits'] >= 1, 'браузер в строгом режиме должен открыть страницу'
    assert state['saved'] == 0


def test_action_and_typed_assertion_share_live_context_and_checkpoint(clients, monkeypatch, publication):
    state, port = publication
    c, suite = _suite_with_steps(
        clients, monkeypatch, [{'action': 'click', 'target': 'Сменить раздел'}], port,
        section='Заказы',
    )
    response = _run_local(c, suite['suite_id'])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'completed'
    assert body['results'][0]['status'] == 'passed', body['results'][0]
    assert body['results'][0]['business_success'] is None
    assert body['results'][0]['assertions'][0]['actual'] == {'type': 'boolean', 'value': True}
    assert len(body['step_results']) == 1
    step = body['step_results'][0]
    assert (step['proposal_index'], step['case_index'], step['step_index']) == (0, 0, 1)
    assert step['status'] == 'passed'
    assert step['started_at'] and step['finished_at']
    assert c.get(f"/api/v1/ai/evidence/{step['evidence_id']}").status_code == 200
    assert state['saved'] == 0
