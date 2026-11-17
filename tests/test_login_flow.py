"""Вход в 1С: явная ошибка вместо тихого прохода. Сеть не используется — только HTML-моки."""
from __future__ import annotations

import time

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


def test_failure_message_contains_form_dump(page):
    page.set_content(
        """
        <div id="authWindow">
          <input id="userField" name="userName" type="text" placeholder="Пользователь">
          <input type="password" placeholder="Пароль">
          <button id="loginBtn">Войти</button>
        </div>
        """
    )

    with pytest.raises(RuntimeError) as error:
        _runner().handle_login(page)

    message = str(error.value)
    assert 'форма входа:' in message
    assert 'input[id=userField,name=userName,type=text]' in message
    assert 'input[type=password]' in message
    assert 'button[id=loginBtn]' in message
    assert '#authWindow' in message
    dump_line = message.split('\n')[-1]
    assert 'Пользователь' not in dump_line, 'placeholder не должна попадать в диагностику'
    assert 'Войти' not in dump_line, 'текст кнопки не должен попадать в диагностику'
    assert len(dump_line) <= 300


def test_failure_message_does_not_leak_field_values(page):
    page.set_content(
        """
        <div id="authWindow">
          <input name="userName" type="text" value="ТайныйЛогин">
          <input type="password" value="СЕКРЕТ_ЗАГЛУШКА">
          <button>Войти</button>
        </div>
        """
    )

    with pytest.raises(RuntimeError) as error:
        _runner().handle_login(page)

    message = str(error.value)
    assert 'СЕКРЕТ_ЗАГЛУШКА' not in message, 'значение пароля не должно попадать в диагностику'
    assert 'ТайныйЛогин' not in message, 'значения полей не собираются вовсе'
    assert 'value=' not in message, 'атрибут value в дамп не попадает'


def test_successful_login_has_no_dump(page):
    page.set_content(
        """
        <div id="authWindow">
          <input name="userName" type="text">
          <button onclick="document.body.innerHTML = '<div>Продажи</div>';">Войти</button>
        </div>
        """
    )

    _runner().handle_login(page)  # ошибки нет — дампа тоже нет

    assert 'форма входа:' not in page.inner_text('body')


def test_slow_workspace_within_grace_has_no_error(page, monkeypatch):
    page.set_content(
        """
        <div id="authWindow">
          <input name="userName" type="text">
          <button onclick="setTimeout(() => { document.body.innerHTML = '<div>Продажи</div>'; }, 1200);">Войти</button>
        </div>
        """
    )
    from testpilot.integration.webclient import WebClientRunner

    monkeypatch.setattr(WebClientRunner, 'LOGIN_GRACE_SECONDS', 3.0)

    _runner().handle_login(page)  # рабочая область появилась в пределах grace — ошибки нет

    assert 'Продажи' in page.inner_text('body')


CONFIRMED_FORM_TOP = """
<script>window.__clicks = 0; window.__fills = 0; window.__login = null;</script>
<div id="authWindow">
  <input id="authWindow_basic_login" name="login" type="text"
         oninput="window.__fills = (window.__fills || 0) + 1;">
  <input id="authWindow_basic_password" name="password" type="password">
  <button id="authWindow_basic_okButton" onclick="window.__clicks = (window.__clicks || 0) + 1;
           window.__login = document.getElementById('authWindow_basic_login').value;
           document.body.innerHTML = '<div>Продажи</div>';">Войти</button>
</div>
"""


def test_confirmed_ids_are_filled_and_clicked_once(page):
    page.set_content(CONFIRMED_FORM_TOP)

    _runner().handle_login(page)

    assert page.evaluate('window.__login') == 'Администратор'
    assert page.evaluate('window.__clicks') == 1
    assert page.evaluate('window.__fills') == 1, 'поле логина заполняется ровно один раз'
    assert page.locator('#authWindow_basic_login').count() == 0


def test_confirmed_form_inside_iframe_is_found(page):
    inner = """<div id="authWindow">
      <input id="authWindow_basic_login" name="login" type="text">
      <input id="authWindow_basic_password" name="password" type="password">
      <button id="authWindow_basic_okButton" onclick="window.__clicks = (window.__clicks || 0) + 1;
               window.__login = document.getElementById('authWindow_basic_login').value;
               document.getElementById('authWindow').remove();
               parent.document.body.insertAdjacentHTML('beforeend', '<div>Продажи</div>');">Войти</button>
    </div>"""
    import html

    page.set_content('<main><iframe id="client" srcdoc="' + html.escape(inner, quote=True) + '"></iframe></main>')
    page.wait_for_timeout(300)

    _runner().handle_login(page)

    client = next(frame for frame in page.frames if frame != page.main_frame)
    assert client.evaluate('window.__login') == 'Администратор'
    assert client.evaluate('window.__clicks') == 1
    assert 'Продажи' in page.inner_text('body')


def test_no_form_in_any_frame_reports_frame_diagnostics(page):
    page.set_content('<main><div id="empty">пусто</div></main>')
    runner = _runner()
    runner.LOGIN_WAIT_SECONDS = 1

    started = time.monotonic()
    with pytest.raises(RuntimeError) as error:
        runner.handle_login(page)
    elapsed = time.monotonic() - started

    message = str(error.value)
    assert 'форма входа не найдена ни в одном фрейме' in message
    assert 'фреймов: 1' in message
    assert 'origin:' in message
    assert 0.9 <= elapsed < 5, f'ожидание должно быть ограничено LOGIN_WAIT_SECONDS, а не 300 мс; прошло {elapsed:.1f} с'


def test_form_appearing_after_delay_is_awaited(page):
    page.set_content(
        """
        <div id="root"></div>
        <script>
          window.__clicks = 0;
          setTimeout(() => {
            document.getElementById('root').innerHTML =
              '<div id="authWindow">' +
              '<input id="authWindow_basic_login" name="login" type="text">' +
              '<input id="authWindow_basic_password" name="password" type="password">' +
              '<button id="authWindow_basic_okButton" onclick="window.__clicks++;' +
              'document.body.innerHTML=\\'<div>Продажи</div>\\'">Войти</button></div>';
          }, 1000);
        </script>
        """
    )
    runner = _runner()
    runner.LOGIN_WAIT_SECONDS = 10

    runner.handle_login(page)

    assert page.evaluate('window.__clicks') == 1
    assert 'Продажи' in page.inner_text('body')


def test_workspace_appearing_before_form_skips_login(page):
    page.set_content(
        """
        <div id="root"></div>
        <script>
          window.__clicks = 0;
          window.__fills = 0;
          setTimeout(() => { document.body.insertAdjacentHTML('beforeend', '<div>Главное</div>'); }, 500);
          setTimeout(() => {
            document.getElementById('root').innerHTML =
              '<div id="authWindow"><input id="authWindow_basic_login" type="text">' +
              '<button id="authWindow_basic_okButton" onclick="window.__clicks++">Войти</button></div>';
          }, 1500);
        </script>
        """
    )
    runner = _runner()
    runner.LOGIN_WAIT_SECONDS = 5

    started = time.monotonic()
    runner.handle_login(page)
    elapsed = time.monotonic() - started

    assert elapsed < 1.4, 'рабочий стол должен завершать ожидание сразу'
    assert page.evaluate('window.__clicks') == 0
    assert page.evaluate('window.__fills') == 0
