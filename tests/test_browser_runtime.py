from types import SimpleNamespace

from testpilot.ai import executor


def test_browser_override_is_required_to_exist(monkeypatch, tmp_path):
    browser = tmp_path / 'custom-browser.exe'
    browser.write_bytes(b'fixture')
    monkeypatch.setenv('TP_CHROMIUM_PATH', str(browser))
    assert executor.browser_path() == str(browser)
    browser.unlink()
    assert executor.browser_path() is None


def test_windows_uses_managed_playwright_browser(monkeypatch, tmp_path):
    browser = tmp_path / 'chrome.exe'
    browser.write_bytes(b'fixture')
    monkeypatch.setattr(executor, 'os', SimpleNamespace(name='nt', environ={}))

    class ManagedBrowser:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(browser)))

        def __exit__(self, *_):
            pass

    monkeypatch.setattr('playwright.sync_api.sync_playwright', ManagedBrowser)
    assert executor.browser_path() == str(browser)
    browser.unlink()
    assert executor.browser_path() is None


def test_container_uses_managed_browser_if_system_chromium_is_absent(monkeypatch, tmp_path):
    browser = tmp_path / 'chrome-linux' / 'chrome'
    browser.parent.mkdir()
    browser.write_bytes(b'fixture')
    real_path = executor.Path

    class MissingSystemBrowser:
        def is_file(self):
            return False

    def fake_path(value):
        return MissingSystemBrowser() if value == '/usr/bin/chromium' else real_path(value)

    monkeypatch.setattr(executor, 'Path', fake_path)
    monkeypatch.setattr(executor, 'os', SimpleNamespace(name='posix', environ={}))

    class ManagedBrowser:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(browser)))

        def __exit__(self, *_):
            pass

    monkeypatch.setattr('playwright.sync_api.sync_playwright', ManagedBrowser)
    assert executor.browser_path() == str(browser)
