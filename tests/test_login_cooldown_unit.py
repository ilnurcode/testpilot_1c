from testpilot.ai import login_guard


class Rows:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


def result(status='error'):
    return [{'engine': 'playwright', 'status': status,
             'message': 'Вход в 1С не выполнен', 'login': {'stage': 'submit', 'form_visible_after': True}}]


def test_cross_project_same_public_target_shares_limit():
    rows = [
        ({'cooldown_key': 'same', 'finished_at': '2099-01-01T00:00:00Z', 'results': result()}, 'draft-old'),
        ({'cooldown_key': 'same', 'finished_at': '2099-01-01T00:00:00Z', 'results': result()}, 'draft-old'),
        ({'cooldown_key': 'other', 'finished_at': '2099-01-01T00:00:00Z', 'results': result()}, 'draft-old'),
    ]
    session = type('S', (), {'execute': lambda self, *a, **k: Rows(rows)})()
    assert login_guard.recent_login_failures(session, login_key='same') == 2
    assert login_guard.recent_login_failures(session, login_key='other') == 1


def test_failure_time_uses_finished_run_not_draft_created_at():
    rows = [
        ({'cooldown_key': 'k', 'created_at': '2099-01-01T00:00:00Z',
          'finished_at': '2000-01-01T00:00:00Z', 'results': result()}, '2099-01-01T00:00:00Z'),
    ]
    session = type('S', (), {'execute': lambda self, *a, **k: Rows(rows)})()
    assert login_guard.recent_login_failures(session, login_key='k') == 0


def test_cooldown_key_is_public_target_plus_principal_without_revision(monkeypatch):
    import testpilot.impact.projects as projects
    public = {'odata_url': 'http://host:8080/odata', 'web_url': None, 'auth_username': 'User',
              'has_password': True, 'connection_revision': 'r1'}
    monkeypatch.setattr(projects, 'connection_public_identity', lambda s, p: public)
    key1 = login_guard.cooldown_key(None, object())
    public['connection_revision'] = 'r2'
    assert login_guard.cooldown_key(None, object()) == key1
    public['auth_username'] = 'Other'
    assert login_guard.cooldown_key(None, object()) != key1


def test_cooldown_normalizes_origin_default_port_and_publication_slash(monkeypatch):
    import testpilot.impact.projects as projects
    public = {'odata_url': 'https://ERP.example.test/ib/odata/', 'web_url': None,
              'auth_username': 'User'}
    monkeypatch.setattr(projects, 'connection_public_identity', lambda s, p: public)
    original = login_guard.cooldown_key(None, object())
    public['odata_url'] = 'https://erp.example.test:443/ib/odata'
    assert login_guard.cooldown_key(None, object()) == original
    public['odata_url'] = 'https://erp.example.test:8443/ib/odata'
    assert login_guard.cooldown_key(None, object()) != original
