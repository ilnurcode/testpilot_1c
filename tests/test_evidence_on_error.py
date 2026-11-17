"""Evidence при ошибке прогона: скриншот сохраняется, а без него результат честно помечается.

Без БД и сети: исполнитель вызывается с подменёнными WebClientRunner.
"""
from __future__ import annotations

from testpilot.ai import executor
from testpilot.integration.webclient import ScenarioReport, StepResult

PNG = b'\x89PNG\r\n\x1a\nunit'


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
