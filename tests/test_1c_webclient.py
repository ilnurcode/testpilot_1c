"""Тесты раннера веб-клиента 1С."""
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
