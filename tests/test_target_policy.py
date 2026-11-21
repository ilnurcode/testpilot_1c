"""Политика сетевых целей: allowlist, порты, redirect/credentials, redaction.

Проверки выполняются до отправки: недопустимая цель не доходит до сетевого клиента.
"""
from __future__ import annotations

import pytest

from testpilot import target_policy as policy


@pytest.fixture(autouse=True)
def clean_policy(monkeypatch):
    monkeypatch.delenv('TP_TARGET_ALLOWLIST', raising=False)
    monkeypatch.delenv('TP_ALLOW_LOCAL_TARGETS', raising=False)


def test_loopback_requires_explicit_opt_in(monkeypatch):
    with pytest.raises(policy.TargetDenied):
        policy.check_target('http://127.0.0.1:8001')
    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
    assert policy.check_target('http://127.0.0.1:8001/') == 'http://127.0.0.1:8001'
    assert policy.check_target('http://localhost:8000') == 'http://localhost:8000'


def test_external_host_requires_allowlist(monkeypatch):
    with pytest.raises(policy.TargetDenied) as error:
        policy.check_target('https://1c.example.test/odata')
    assert 'TP_TARGET_ALLOWLIST' in str(error.value)
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', '1c.example.test,erp.local:8443')
    assert policy.check_target('https://1c.example.test/odata') == 'https://1c.example.test/odata'
    assert policy.check_target('https://erp.local:8443/x') == 'https://erp.local:8443/x'
    with pytest.raises(policy.TargetDenied):
        policy.check_target('https://erp.local:9443/x')


@pytest.mark.parametrize('url', ['ftp://1c.example.test', '1c.example.test',
                                 'https://user:pass@1c.example.test'])
def test_non_http_or_userinfo_is_refused(url, monkeypatch):
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', '1c.example.test')
    with pytest.raises(policy.TargetDenied):
        policy.check_target(url)


def test_redirect_within_origin_allowed_and_cross_origin_refused(monkeypatch):
    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
    assert policy.check_redirect('http://127.0.0.1:8001/odata', '/login') == 'http://127.0.0.1:8001/login'
    with pytest.raises(policy.TargetDenied):
        policy.check_redirect('http://127.0.0.1:8001/odata', 'https://evil.example.test/x')
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', 'erp.local')
    # Даже allowlisted чужой origin по умолчанию отклоняется: credentials не пересылаются.
    with pytest.raises(policy.TargetDenied):
        policy.check_redirect('http://127.0.0.1:8001/odata', 'https://erp.local/x')
    assert policy.check_redirect('http://127.0.0.1:8001/odata', 'https://erp.local/x',
                                 allow_cross_origin=True) == 'https://erp.local/x'
    # https без порта и https:443 — один и тот же origin.
    assert policy.check_redirect('https://erp.local/a', 'https://erp.local:443/b') == 'https://erp.local:443/b'


def test_credentials_are_not_forwarded_to_another_origin(monkeypatch):
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', 'a.test,b.test')
    # Эффективный порт: https без порта и https:443 — один origin.
    assert policy.check_credentials_target('https://a.test/x', 'https://a.test:443/y') == 'https://a.test:443/y'
    with pytest.raises(policy.TargetDenied):
        policy.check_credentials_target('https://a.test/x', 'https://b.test/y')
    with pytest.raises(policy.TargetDenied):
        policy.check_credentials_target('https://a.test/x', 'http://a.test/y')
    with pytest.raises(policy.TargetDenied):
        policy.check_credentials_target('https://a.test/x', 'https://a.test:8443/y')


def test_invalid_allowlist_entry_fails_closed_before_request(monkeypatch):
    for bad in ('host:garbage', 'host:70000', 'bad host', '[::1', 'host::443'):
        monkeypatch.setenv('TP_TARGET_ALLOWLIST', f'good.test,{bad}')
        with pytest.raises(policy.TargetDenied):
            policy.allowlist()
        with pytest.raises(policy.TargetDenied):
            policy.check_target('https://good.test/odata')

    monkeypatch.setenv('TP_TARGET_ALLOWLIST', 'api.example:garbage')
    with pytest.raises(policy.TargetDenied) as error:
        policy.check_target('https://api.example/odata')
    assert 'garbage' not in str(error.value), 'значение записи не печатается'


def test_host_only_entry_matches_default_scheme_ports_only(monkeypatch):
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', 'erp.local')
    assert policy.check_target('https://erp.local/odata') == 'https://erp.local/odata'
    assert policy.check_target('http://erp.local/odata') == 'http://erp.local/odata'
    with pytest.raises(policy.TargetDenied):
        policy.check_target('https://erp.local:8443/odata')
    with pytest.raises(policy.TargetDenied):
        policy.check_target('http://erp.local:8080/odata')


def test_explicit_port_entry_matches_effective_port(monkeypatch):
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', '[2001:db8::1]:8443,erp.local:443')
    assert policy.check_target('https://erp.local/odata') == 'https://erp.local/odata'
    assert policy.check_target('https://[2001:db8::1]:8443/odata') == 'https://[2001:db8::1]:8443/odata'
    with pytest.raises(policy.TargetDenied):
        policy.check_target('http://erp.local/odata')


def test_redaction_returns_only_safe_origin():
    redacted = policy.redact_url('https://user:secret@1c.example.test/odata?token=abc&x=1')
    assert redacted == 'https://1c.example.test'
    assert 'secret' not in redacted and 'abc' not in redacted
    assert policy.redact_url('https://1c.example.test/odata') == 'https://1c.example.test'
    assert policy.redact_url('http://127.0.0.1:8001/x') == 'http://127.0.0.1:8001'
    assert policy.redact_url('https://1c.example.test:notaport/x') == '<некорректный URL>'
    masked = policy.redact_headers({'Authorization': 'Basic abc', 'Cookie': 'sid=1', 'Accept': 'json'})
    assert masked == {'Authorization': '…', 'Cookie': '…', 'Accept': 'json'}
