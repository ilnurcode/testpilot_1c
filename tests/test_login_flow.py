"""Вход в 1С: явная ошибка вместо тихого прохода. Сеть не используется — только HTML-моки."""
from __future__ import annotations

import pytest

from testpilot.integration.probe import Profile
from testpilot.integration.webclient import WebClientRunner

LOGIN_FAILED = 'Вход в 1С не выполнен: форма логина осталась на экране'


def _runner() -> WebClientRunner:
    profile = Profile(odata_url='http://127.0.0.1:59999/DemoTrd/odata/standard.odata',
                      web_url='http://127.0.0.1:59999/DemoTrd', username='Администратор', password='')
    return WebClientRunner(profile)


@pytest.fixture(scope='module')
def page():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as error:  # pragma: no cover - окружение без Chromium
            pytest.skip(f'Chromium недоступен: {error}')
        context = browser.new_context()
        target = context.new_page()
        try:
            yield target
        finally:
            context.close()
            browser.close()


def test_login_fills_user_and_passes(page):
    page.set_content(
        """
        <div id="authWindow">
          <input name="userName" type="text" value="">
          <input type="password" value="">
          <button onclick="window.__login = document.querySelector('input[name=userName]').value;
                           document.body.innerHTML = '<div>Продажи</div>';">Войти</button>
        </div>
        """
    )
    runner = _runner()

    runner.handle_login(page)

    assert page.evaluate('window.__login') == 'Администратор'
    assert page.locator('input[name="userName"]').count() == 0, 'после успешного входа форма должна исчезнуть'


def test_nonstandard_login_form_raises_explicit_error(page):
    page.set_content(
        """
        <div class="login-box">
          <input id="q" name="q" type="text">
          <input type="password">
          <button>Войти</button>
        </div>
        """
    )
    runner = _runner()

    with pytest.raises(RuntimeError) as error:
        runner.handle_login(page)

    assert LOGIN_FAILED in str(error.value)
    assert 'селектор не найден или кнопка «Войти» не сработала' in str(error.value)


def test_no_login_form_means_already_signed_in(page):
    page.set_content('<main><div id="funcPanel">Продажи</div></main>')

    _runner().handle_login(page)  # не должно бросить


def test_login_button_without_effect_raises(page):
    page.set_content(
        """
        <div id="authWindow">
          <input name="userName" type="text" value="">
          <input type="password" value="">
          <button>Войти</button>
          <div>Продажи</div>
        </div>
        """
    )
    runner = _runner()

    with pytest.raises(RuntimeError) as error:
        runner.handle_login(page)

    assert LOGIN_FAILED in str(error.value)


def test_empty_password_submits_exactly_once(page):
    page.set_content(
        """
        <div id="authWindow">
          <script>window.__clicks = 0;</script>
          <input name="userName" type="text" value="">
          <input type="password" value="">
          <button onclick="window.__clicks = (window.__clicks || 0) + 1;
                           document.body.innerHTML = '<div>Продажи</div>';">Войти</button>
        </div>
        """
    )
    runner = _runner()  # password='' — пустой пароль легитимен

    runner.handle_login(page)

    assert page.evaluate('window.__clicks') == 1, 'вход должен выполняться ровно одной попыткой'
    assert 'Продажи' in page.inner_text('body')


def test_platform_error_text_is_reported(page):
    page.set_content(
        """
        <div id="authWindow">
          <script>window.__clicks = 0;</script>
          <input name="userName" type="text" value="">
          <input type="password" value="">
          <div id="err" style="display:none">Неверный пароль</div>
          <button onclick="window.__clicks = (window.__clicks || 0) + 1;
                           document.getElementById('err').style.display = 'block';">Войти</button>
        </div>
        """
    )
    runner = _runner()

    with pytest.raises(RuntimeError) as error:
        runner.handle_login(page)

    message = str(error.value)
    assert LOGIN_FAILED in message
    assert 'Неверный пароль' in message, f'текст платформы должен попасть в ошибку: {message}'
    assert 'пароль пустой — как настроено в проекте' in message


def test_platform_error_does_not_trigger_retry(page):
    page.set_content(
        """
        <div id="authWindow">
          <script>window.__clicks = 0;</script>
          <input name="userName" type="text" value="">
          <input type="password" value="">
          <div id="err" style="display:none">Превышено количество попыток</div>
          <button onclick="window.__clicks = (window.__clicks || 0) + 1;
                           document.getElementById('err').style.display = 'block';">Войти</button>
        </div>
        """
    )
    runner = _runner()

    with pytest.raises(RuntimeError) as error:
        runner.handle_login(page)

    assert 'Превышено количество попыток' in str(error.value)
    assert page.evaluate('window.__clicks') == 1, 'повторных кликов быть не должно'
