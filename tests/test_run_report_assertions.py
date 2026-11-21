"""Потребительский контракт отчётов: typed assertions, неполный прогон, безопасный экспорт. Без БД."""
import csv
import io
import json

import pytest

from testpilot.ai.models import CaseResult
from testpilot.web.run_report import render_run_report


def _suite(status='completed'):
    result = CaseResult.model_validate({
        'status': 'failed', 'business_success': False, 'message': 'Сумма отличается',
        'assertions': [
            {'assertion_id': 'sum', 'scope': 'business', 'status': 'failed',
             'expected': {'type': 'number', 'value': 120.5},
             'actual': {'type': 'number', 'value': 119.5}, 'message': 'Расхождение суммы'},
            {'assertion_id': 'service', 'scope': 'connectivity', 'status': 'passed',
             'expected': {'type': 'boolean', 'value': True},
             'actual': {'type': 'boolean', 'value': True}, 'message': 'Доступно'},
            {'assertion_id': 'question', 'scope': 'manual', 'status': 'skipped',
             'expected': None, 'actual': None, 'message': 'Уточните требование'},
        ],
    }).model_dump()
    result.update(proposal_index=0, case_index=1, title='Сумма', engine='odata',
                  duration_seconds=0.25, evidence_id='saved-evidence', evidence_provenance='none',
                  screenshot_unavailable_reason='Ошибка отрисовки', error_provenance={'stage': 'assertion'})
    return {
        'suite_id': 'saved-suite', 'status': status, 'business_success': False,
        'message': 'Сохранённый диагноз', 'counts': {'passed': 0, 'failed': 1, 'error': 0, 'skipped': 0},
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{'title': 'Сумма', 'engine': 'odata', 'cases': [
            {'entity': 'First', 'check': 'count'}, {'entity': 'Second', 'check': 'field'},
        ]}]},
        'results': [result],
    }


def _csv(suite):
    response = render_run_report(suite, 'Проект', 'csv')
    return list(csv.DictReader(io.StringIO(response.body.decode('utf-8')), delimiter=';'))


def test_exports_preserve_typed_assertions_and_each_unexecuted_case():
    suite = _suite('cancelled')
    rows = _csv(suite)
    assert len(rows) == 2
    actual, missing = rows
    assert actual['Case'] == '2'
    assert actual['Проверка'] == 'Second · field'
    assert actual['Статус'] == 'failed'
    assert actual['Бизнес-результат'] == 'не подтверждён'
    assert actual['Длительность, мс'] == '250'
    assert actual['Статус прогона'] == 'cancelled'
    assert actual['Evidence'] == 'saved-evidence'
    assert actual['Источник evidence'] == 'none'
    assert actual['Причина отсутствия снимка'] == 'Ошибка отрисовки'
    assert json.loads(actual['Источник ошибки']) == {'stage': 'assertion'}
    assert json.loads(actual['Assertions']) == suite['results'][0]['assertions']
    assert missing['Case'] == '1' and missing['Проверка'] == 'First · count'
    assert missing['Статус'] == 'not_performed' and missing['Сообщение'] == 'не выполнено: результата нет'
    body = render_run_report(suite, 'Проект', 'md').body.decode()
    assert 'case 2: failed' in body and r'case 1: not\_performed' in body
    assert 'scope=business, status=failed' in body
    assert 'scope=connectivity, status=passed' in body
    assert 'scope=manual, status=skipped' in body
    assert '120.5' in body and '119.5' in body
    assert 'Статус: cancelled' in body
    assert 'Источник ошибки:' in body
    assert actual['Сообщение прогона'] == 'Сохранённый диагноз'


@pytest.mark.parametrize('status', ['queued', 'running', 'completed', 'failed', 'error', 'cancelled', 'interrupted'])
def test_terminal_and_pending_status_is_not_inferred_from_case_counts(status):
    suite = _suite(status)
    suite['results'][0].update(status='passed', business_success=None)
    suite['results'][0]['assertions'] = [suite['results'][0]['assertions'][1]]
    suite['business_success'] = None
    suite['counts'] = {'passed': 1, 'failed': 0, 'error': 0, 'skipped': 0}
    body = render_run_report(suite, 'Проект', 'md').body.decode()
    assert f'Статус: {status}' in body
    assert 'Бизнес-результат: не проверен / не доказан' in body
    assert 'Connectivity/metadata passed не подтверждают' in body
    assert _csv(suite)[0]['Бизнес-результат'] == 'не проверен / не доказан'


def test_export_keeps_orphaned_saved_case_instead_of_silently_dropping_it():
    suite = _suite()
    suite['results'][0]['proposal_index'] = 99
    rows = _csv(suite)
    assert len(rows) == 3
    assert rows[0]['Предложение'] == 'Сумма' and rows[0]['Статус'] == 'failed'
    body = render_run_report(suite, 'Проект', 'md').body.decode()
    assert 'case 2: failed' in body


def test_export_does_not_execute_html_markdown_links_or_spreadsheet_formulas():
    suite = _suite('interrupted')
    hostile = '<script>alert(1)</script> [click](https://evil.invalid)\n# forged'
    suite['results'][0]['message'] = hostile
    suite['results'][0]['title'] = '=HYPERLINK("https://evil.invalid")'
    suite['suggestion']['proposals'][0]['title'] = hostile
    suite['results'][0]['assertions'][0]['message'] = hostile
    body = render_run_report(suite, hostile, 'md').body.decode()
    assert '<script>' not in body
    assert '[click](https://evil.invalid)' not in body
    assert '\n# forged' not in body
    rows = _csv(suite)
    assert rows[0]['Предложение'].startswith("'=HYPERLINK")
    assert rows[0]['Сообщение'] == hostile
    assert json.loads(rows[0]['Assertions'])[0]['message'] == hostile


@pytest.mark.parametrize('status', ['error', 'cancelled', 'interrupted'])
def test_no_results_does_not_claim_run_never_started(status):
    suite = _suite(status)
    suite['results'] = []
    body = render_run_report(suite, 'Проект', 'md').body.decode()
    assert 'Сохранённых результатов кейсов нет; успешность не доказана.' in body
    assert 'прогон ещё не выполнялся' not in body
    assert len(_csv(suite)) == 2


def test_report_names_metadata_probe_instead_of_claiming_collection_read():
    suite = _suite()
    suite['suggestion']['proposals'][0]['cases'][1]['assertions'] = [
        {'id': 'probe-meta', 'scope': 'metadata', 'check': 'metadata_entity'},
    ]
    row = _csv(suite)[0]
    assert row['Проверка'] == 'Second · metadata: metadata_entity'
    body = render_run_report(suite, 'Проект', 'md').body.decode()
    assert r'Second · metadata: metadata\_entity' in body
    assert 'чтение коллекции' not in body
