"""Числовые настройки окружения: единый хелпер, диапазоны, проверка на старте."""
import pytest

from testpilot import audit_limit, config
from testpilot.ai import codex

NUMERIC_VARS = [
    # (переменная, дефолт, минимум, максимум)
    ('TP_AUDIT_MAX_ISSUES', 5000, 100, 20000),
    ('TP_CODEX_TIMEOUT_SECONDS', 180, 1, 900),
    ('TP_1C_LOGIN_GRACE_SECONDS', 10, 2, 60),
]


@pytest.mark.parametrize('name,default,minimum,maximum', NUMERIC_VARS)
def test_numeric_env_uses_default_when_absent_or_empty(monkeypatch, name, default, minimum, maximum):
    monkeypatch.delenv(name, raising=False)
    assert config.int_env(name, default, minimum, maximum) == default
    assert config.int_env(name, default, minimum, maximum, raw='') == default
    assert config.int_env(name, default, minimum, maximum, raw='   ') == default


@pytest.mark.parametrize('name,default,minimum,maximum', NUMERIC_VARS)
def test_numeric_env_accepts_valid_values_and_bounds(monkeypatch, name, default, minimum, maximum):
    monkeypatch.setenv(name, str(minimum))
    assert config.int_env(name, default, minimum, maximum) == minimum
    monkeypatch.setenv(name, str(maximum))
    assert config.int_env(name, default, minimum, maximum) == maximum


@pytest.mark.parametrize('raw', ['abc', '0', '-1', '1e5', ' 5 000'])
def test_numeric_env_rejects_garbage_with_name_and_range(raw):
    name, default, minimum, maximum = 'TP_CODEX_TIMEOUT_SECONDS', 180, 1, 900

    with pytest.raises(ValueError) as error:
        config.int_env(name, default, minimum, maximum, raw=raw)

    message = str(error.value)
    assert name in message
    assert str(minimum) in message and str(maximum) in message


@pytest.mark.parametrize('name,default,minimum,maximum', NUMERIC_VARS)
def test_numeric_env_rejects_values_above_maximum(name, default, minimum, maximum):
    with pytest.raises(ValueError) as error:
        config.int_env(name, default, minimum, maximum, raw=str(maximum + 1))

    message = str(error.value)
    assert name in message
    assert f'{minimum} до {maximum}' in message


def test_audit_limit_reuses_the_shared_helper():
    assert audit_limit.int_env is config.int_env

    assert audit_limit.resolve_max_issues_kept('100') == 100
    with pytest.raises(ValueError) as error:
        audit_limit.resolve_max_issues_kept('99')
    assert 'TP_AUDIT_MAX_ISSUES' in str(error.value)
    assert '100 до 20000' in str(error.value)


def test_codex_timeout_is_read_once_and_reaches_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv('TP_CODEX_MODEL', '')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr(codex, 'CODEX_TIMEOUT_SECONDS', 300)

    configuration = codex._configuration()

    assert configuration.timeout == 300


def test_codex_timeout_defaults_to_180_when_unset(monkeypatch):
    monkeypatch.delenv('TP_CODEX_TIMEOUT_SECONDS', raising=False)

    assert config.int_env('TP_CODEX_TIMEOUT_SECONDS', 180, 1, 900) == 180
    assert codex.CODEX_TIMEOUT_SECONDS == 180


def test_login_grace_is_configurable_and_wired(monkeypatch):
    from testpilot.integration.webclient import WebClientRunner

    monkeypatch.delenv('TP_1C_LOGIN_GRACE_SECONDS', raising=False)
    assert config.int_env('TP_1C_LOGIN_GRACE_SECONDS', 10, 2, 60) == 10
    assert config.int_env('TP_1C_LOGIN_GRACE_SECONDS', 10, 2, 60, raw='2') == 2
    assert WebClientRunner.LOGIN_GRACE_SECONDS == config.int_env('TP_1C_LOGIN_GRACE_SECONDS', 10, 2, 60)

    for bad in ('1', '61', 'abc', '0'):
        with pytest.raises(ValueError) as error:
            config.int_env('TP_1C_LOGIN_GRACE_SECONDS', 10, 2, 60, raw=bad)
        message = str(error.value)
        assert 'TP_1C_LOGIN_GRACE_SECONDS' in message
        assert 'от 2 до 60' in message
