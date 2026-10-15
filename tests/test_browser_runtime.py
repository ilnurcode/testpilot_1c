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
