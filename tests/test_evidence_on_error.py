"""Evidence при ошибке прогона: скриншот сохраняется, а без него результат честно помечается.

Без БД и сети: исполнитель вызывается с подменёнными WebClientRunner.
"""
from __future__ import annotations

from testpilot.ai import executor
from testpilot.integration.webclient import ScenarioReport, StepResult

PNG = b'\x89PNG\r\n\x1a\n' + b'screenshot-payload' * 10  # >100 байт, как настоящий снимок


def _suite() -> dict:
    return {
        'approval': {'selected_proposals': [0]},
        'suggestion': {
            'proposals': [{
                'title': 'Веб-клиент 1С',
                'engine': 'playwright',
                'object_ids': ['document.заказклиента'],
                'steps': ['открыть раздел'],
                'expected_result': 'Раздел открыт',
                'cases': [{'steps': [{'action': 'click', 'target': 'Продажи'}]}],
            }],
        },
    }


class _Profile:
    odata_url = None
    web_url = 'http://127.0.0.1:59999/DemoTrd'


def _patch_runner(monkeypatch, report: ScenarioReport, diagnostics: dict | None = None):
    import testpilot.integration.webclient as webclient_module

    class FakeRunner:
        def __init__(self, profile):
            self.profile = profile
            self.last_login_diagnostics = diagnostics

        def run_scenario(self, title, steps):
            return report

    monkeypatch.setattr(webclient_module, 'WebClientRunner', FakeRunner)


def test_step_failure_keeps_screenshot_evidence(monkeypatch):
    report = ScenarioReport(
        title='Веб-клиент 1С', status='failed',
        error='Ошибка на шаге 1 (click Продажи): элемент не найден',
        screenshots=[PNG],
        steps=[StepResult(1, 'click', 'Продажи', None, 'failed', 'элемент не найден', 0.2, PNG)],
    )
    _patch_runner(monkeypatch, report)

    results, evidence = executor.execute_1c(_suite(), _Profile())

    assert results[0]['status'] == 'failed'
    assert evidence == [(0, PNG)], 'скриншот ошибки шага должен попасть в evidence'
    assert 'screenshot_unavailable' not in results[0]


def test_login_failure_keeps_screenshot_and_diagnostics(monkeypatch):
    report = ScenarioReport(
        title='Веб-клиент 1С', status='error',
        error='Вход в 1С не выполнен: форма логина осталась на экране (селектор не найден или кнопка «Войти» не сработала)',
        screenshots=[PNG],
        steps=[StepResult(0, 'open_url', 'http://127.0.0.1:59999/DemoTrd', None, 'failed',
                          'Вход в 1С не выполнен', 3.0, PNG)],
    )
    diagnostics = {'stage': 'after_click', 'user_filled': True, 'password_filled': False,
                   'clicked': True, 'form_visible_after': True}
    _patch_runner(monkeypatch, report, diagnostics)

    results, evidence = executor.execute_1c(_suite(), _Profile())

    assert results[0]['status'] == 'error'
    assert evidence == [(0, PNG)]
    assert results[0]['login'] == diagnostics
    assert results[0]['login']['clicked'] is True
    assert results[0]['login']['form_visible_after'] is True


def test_missing_screenshot_is_reported_honestly(monkeypatch):
    report = ScenarioReport(title='Веб-клиент 1С', status='error',
                            error='Вход в 1С не выполнен: форма логина осталась на экране')
    _patch_runner(monkeypatch, report, {'stage': 'timeout', 'user_filled': False,
                                        'password_filled': False, 'clicked': False,
                                        'form_visible_after': None})

    results, evidence = executor.execute_1c(_suite(), _Profile())

    assert evidence == []
    assert results[0]['screenshot_unavailable'] is True
    assert results[0]['screenshot_unavailable_reason']


def test_empty_or_tiny_screenshot_is_not_saved(monkeypatch):
    report = ScenarioReport(title='Веб-клиент 1С', status='error',
                            error='Вход в 1С не выполнен: форма логина осталась на экране',
                            screenshots=[b'', b'\x89PNG'])
    _patch_runner(monkeypatch, report, {'stage': 'after_click', 'user_filled': True,
                                        'password_filled': False, 'clicked': True,
                                        'form_visible_after': True})

    results, evidence = executor.execute_1c(_suite(), _Profile())

    assert evidence == [], 'пустой или крошечный снимок не должен сохраняться'
    assert results[0]['screenshot_unavailable'] is True
    assert 'снимок пуст' in results[0]['screenshot_unavailable_reason']


def test_empty_post_submit_uses_pre_submit_screenshot(monkeypatch):
    pre = b'\x89PNG\r\n\x1a\n' + b'pre-submit-form' * 10
    report = ScenarioReport(title='Веб-клиент 1С', status='error',
                            error='Вход в 1С не выполнен: форма логина осталась на экране',
                            screenshots=[b''], pre_submit_screenshot=pre)
    _patch_runner(monkeypatch, report, {'stage': 'after_click', 'user_filled': True,
                                        'password_filled': False, 'clicked': True,
                                        'form_visible_after': True})

    results, evidence = executor.execute_1c(_suite(), _Profile())

    assert evidence == [(0, pre)], 'fallback должен взять снимок формы до отправки'
    assert 'screenshot_unavailable' not in results[0]


def test_both_post_and_pre_submit_empty_marks_unavailable(monkeypatch):
    report = ScenarioReport(title='Веб-клиент 1С', status='error',
                            error='Вход в 1С не выполнен', screenshots=[b''],
                            pre_submit_screenshot=b'')
    _patch_runner(monkeypatch, report, {'stage': 'after_click', 'user_filled': False,
                                        'password_filled': False, 'clicked': True,
                                        'form_visible_after': True})

    results, evidence = executor.execute_1c(_suite(), _Profile())

    assert evidence == []
    assert results[0]['screenshot_unavailable'] is True
    assert 'снимок пуст' in results[0]['screenshot_unavailable_reason']


def test_successful_run_saves_one_evidence_only(monkeypatch):
    first = b'\x89PNG\r\n\x1a\n' + b'open-url' * 20
    last = b'\x89PNG\r\n\x1a\n' + b'final-step' * 20
    report = ScenarioReport(title='Веб-клиент 1С', status='passed', screenshots=[first, last])
    _patch_runner(monkeypatch, report, {'stage': 'after_click', 'user_filled': True,
                                        'password_filled': False, 'clicked': True,
                                        'form_visible_after': False})

    results, evidence = executor.execute_1c(_suite(), _Profile())

    assert results[0]['status'] == 'passed'
    assert len(evidence) == 1, 'на результат — ровно один evidence, без дубликатов'
    assert evidence[0] == (0, last), 'берётся последний осмысленный снимок шага'
