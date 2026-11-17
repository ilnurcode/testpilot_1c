"""md-отчёт прогона: строка про источник evidence и размеры снимков. Без БД и сети."""
from __future__ import annotations

from testpilot.web.run_report import render_run_report


def _suite(result: dict) -> dict:
    return {
        'suite_id': 'suite-1',
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Веб-клиент 1С',
            'engine': 'playwright',
            'object_ids': ['document.x'],
            'steps': ['открыть раздел'],
            'expected_result': 'Раздел открыт',
            'cases': [{'steps': [{'action': 'click', 'target': 'Продажи'}]}],
        }]},
        'results': [result],
    }


def test_report_prints_evidence_provenance_and_sizes():
    result = {
        'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Веб-клиент 1С',
        'status': 'error', 'message': 'Вход в 1С не выполнен', 'duration_seconds': 21.5,
        'evidence_id': 'eid-1', 'evidence_provenance': 'step',
        'post_click_screenshot_bytes': 2000, 'pre_submit_screenshot_bytes': 1500,
    }

    body = render_run_report(_suite(result), 'Проект', 'md').body.decode('utf-8')

    assert '- Evidence: step, пост-клик 2000 Б, до отправки 1500 Б' in body


def test_report_prints_no_screenshot_reason():
    result = {
        'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Веб-клиент 1С',
        'status': 'error', 'message': 'Вход в 1С не выполнен', 'duration_seconds': 21.5,
        'evidence_provenance': 'none', 'screenshot_unavailable': True,
        'screenshot_unavailable_reason': 'снимок пуст: страница не отрисована или браузер закрылся',
        'post_click_screenshot_bytes': 0, 'pre_submit_screenshot_bytes': 0,
    }

    body = render_run_report(_suite(result), 'Проект', 'md').body.decode('utf-8')

    assert '- Evidence: нет снимка: снимок пуст: страница не отрисована или браузер закрылся' in body
    assert 'пост-клик 0 Б, до отправки 0 Б' in body
