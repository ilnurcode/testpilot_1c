"""URL-настройки окружения: строгая проверка на старте."""
import pytest

from testpilot import config
from testpilot.ai import provider


@pytest.mark.parametrize('value', ['https://api.example.com/v1', 'http://127.0.0.1:8001', 'https://1c.example.test/odata/standard.odata'])
def test_url_env_accepts_http_and_https(value):
    assert config.url_env('PROBE_URL', raw=value) == value


@pytest.mark.parametrize('value', ['htp://x', 'x', 'http://', ' https://x ', 'https:// x', 'postgresql://db:5432/x'])
def test_url_env_rejects_invalid_values_with_name(value):
    with pytest.raises(ValueError) as error:
        config.url_env('TP_LLM_BASE_URL', raw=value)

    message = str(error.value)
    assert 'TP_LLM_BASE_URL' in message
    assert 'http' in message and 'https' in message
    assert repr(value) in message or value in message


def test_url_env_empty_value_means_default_or_unset(monkeypatch):
    monkeypatch.delenv('TP_1C_ODATA_URL', raising=False)

    assert config.url_env('TP_1C_ODATA_URL', raw='') is None
    assert config.url_env('TP_1C_ODATA_URL') is None
    assert config.url_env('IMPACT_URL', default='http://127.0.0.1:8001', raw='   ') == 'http://127.0.0.1:8001'


def test_url_env_required_rejects_empty():
    with pytest.raises(ValueError) as error:
        config.url_env('IMPACT_URL', required=True, raw='')

    assert 'IMPACT_URL' in str(error.value)
    assert 'обязательна' in str(error.value)


def test_database_url_accepts_postgresql_schemes_only():
    assert config.url_env('DATABASE_URL', schemes=config.DATABASE_SCHEMES,
                          raw='postgresql+psycopg://user:pass@db:5432/name') == 'postgresql+psycopg://user:pass@db:5432/name'
    assert config.url_env('DATABASE_URL', schemes=config.DATABASE_SCHEMES,
                          raw='postgresql://user:pass@db:5432/name') == 'postgresql://user:pass@db:5432/name'

    with pytest.raises(ValueError) as error:
        config.url_env('DATABASE_URL', schemes=config.DATABASE_SCHEMES, raw='mysql://user:pass@db:3306/name')

    message = str(error.value)
    assert 'DATABASE_URL' in message
    assert 'postgresql+psycopg' in message and 'postgresql' in message


@pytest.mark.parametrize('name', ['TP_LLM_BASE_URL', 'IMPACT_URL', 'INTELLIGENCE_URL', 'WEB_URL', 'TP_1C_ODATA_URL', 'TP_1C_WEB_URL', 'DATABASE_URL'])
def test_startup_validation_covers_every_url_variable(monkeypatch, name):
    monkeypatch.setenv(name, 'htp://broken')

    with pytest.raises(ValueError) as error:
        config.validate_startup_urls()

    assert name in str(error.value)


def test_valid_urls_pass_startup_validation(monkeypatch):
    values = {
        'TP_LLM_BASE_URL': 'https://api.example.com/v1',
        'IMPACT_URL': 'http://impact:8001',
        'INTELLIGENCE_URL': 'http://intelligence:8002',
        'WEB_URL': 'http://web:8000',
        'TP_1C_ODATA_URL': 'https://1c.example.test/odata/standard.odata',
        'TP_1C_WEB_URL': 'http://1c.example.test/web',
        'DATABASE_URL': 'postgresql+psycopg://user:pass@db:5432/name',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    config.validate_startup_urls()


def test_llm_base_url_reaches_provider_configuration(monkeypatch):
    monkeypatch.setenv('TP_LLM_BASE_URL', 'https://llm.example.test/v1')
    monkeypatch.setenv('TP_LLM_API_KEY', 'probe-key')
    monkeypatch.setenv('TP_LLM_MODEL', 'probe-model')

    base, model, key = provider.configuration()

    assert base == 'https://llm.example.test/v1'
    assert model == 'probe-model' and key == 'probe-key'
