import base64
"""Playwright-адаптер для автоматизации сценариев в веб-клиенте 1С:Предприятие."""
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from testpilot.ai.executor import browser_path
from testpilot.config import choice_env, int_env
from testpilot.integration.probe import Profile, container_access_url, container_access_urls


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or '').lower()
        port = parsed.port or (443 if scheme == 'https' else 80 if scheme == 'http' else None)
    except ValueError:
        return None
    if scheme not in {'http', 'https'} or not host or port is None:
        return None
    return scheme, host, port

# Откат с mapped-адреса допустим только для ошибок уровня соединения: отказ, неразрешимое имя
# или недостижимый адрес. Таймауты (загрузки документа и локаторов) и ответы 401/403 сюда не входят:
# таймаут не отличает медленный сервер от недоступного, а откат лишь удвоил бы ожидание.
NAVIGATION_NETWORK_ERRORS = (
    'ERR_NAME_NOT_RESOLVED',
    'ERR_CONNECTION_REFUSED',
    'ERR_ADDRESS_UNREACHABLE',
)


def navigation_error_is_connection_level(error: BaseException) -> bool:
    """Ошибка уровня соединения — единственный повод повторить переход по другому адресу.

    Таймауты и прочие сбои загрузки сюда не входят: они не отличают недоступный хост
    от медленного сервера, а откат лишь удвоил бы ожидание.
    """
    message = str(error)
    return any(marker in message for marker in NAVIGATION_NETWORK_ERRORS)


def navigation_error_is_transient(error: BaseException) -> bool:
    """Гонка с предыдущей неудачной навигацией Chrome (chrome-error): тот же адрес стоит повторить."""
    return 'interrupted by another navigation' in str(error)


def _settle_navigation(page) -> None:
    """Даёт Chrome завершить предыдущую навигацию: иначе следующий переход прерывается остатком chrome-error."""
    try:
        page.wait_for_load_state('domcontentloaded', timeout=2000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(150)
    except Exception:
        pass


def navigate(page, url: str, addresses: list[str] | None = None, timeout: float = 45000) -> str:
    """Открывает публикацию по адресам container_access_urls: mapped, затем исходный loopback.

    `addresses` позволяет передать уже вычисленные адреса публикации (например, для open_url).
    Откат на следующий адрес выполняется только для ошибок уровня соединения
    (ERR_NAME_NOT_RESOLVED / ERR_CONNECTION_REFUSED / ERR_ADDRESS_UNREACHABLE);
    таймаут загрузки документа и ответы 401/403 пробрасываются без второго адреса.
    Гонка с остатком chrome-error даёт один повтор того же адреса.
    Возвращает адрес, по которому документ открылся.
    """
    addresses = list(addresses) if addresses else container_access_urls(url)
    for index, address in enumerate(addresses):
        for attempt in range(2):
            try:
                page.goto(address, wait_until='domcontentloaded', timeout=timeout)
            except Exception as error:
                if navigation_error_is_transient(error):
                    _settle_navigation(page)
                    if attempt == 0:
                        continue
                    raise
                if index == len(addresses) - 1 or not navigation_error_is_connection_level(error):
                    raise
                _settle_navigation(page)
                break
            return address
    raise AssertionError('Список адресов публикации пуст')

@dataclass
class StepResult:
    step_number: int
    action: str
    target: str
    value: Any
    status: str
    message: str
    duration_seconds: float
    screenshot: bytes | None = None

@dataclass
class ScenarioReport:
    title: str
    status: str
    steps: list[StepResult] = field(default_factory=list)
    total_duration_seconds: float = 0.0
    error: str | None = None
    screenshots: list[bytes] = field(default_factory=list)
    url: str | None = None
    # Снимок формы входа до отправки: fallback, если пост-кликовый снимок пуст.
    pre_submit_screenshot: bytes = b''
    # Стадия каждого снимка из screenshots: pre_submit | post_click | step_failure | step_ok.
    screenshot_stages: list[str] = field(default_factory=list)


def _safe_screenshot(context, page) -> bytes:
    try:
        cdp = context.new_cdp_session(page)
        res = cdp.send('Page.captureScreenshot', {'format': 'png'})
        cdp.detach()
        return base64.b64decode(res['data'])
    except Exception:
        return page.screenshot(full_page=False, animations='disabled', timeout=15000)

def _workspace_lines(page) -> list[str]:
    script = """() => {
      const visible = element => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
      const root = document.querySelector('#funcPanel') ||
        [...document.querySelectorAll('main,[role="main"],[role="application"],#workspace,.workspace,.frameMain,.frameWork,.frameContent')]
          .filter(visible).sort((left, right) => (right.innerText || '').length - (left.innerText || '').length)[0];
      if (!root) return [];
      return (root.innerText || '').split(/\\n/).map(line => line.trim()).filter(Boolean);
    }"""
    lines = []
    for frame in page.frames:
        try:
            lines.extend(frame.evaluate(script))
        except Exception:
            continue
    return lines


def assert_section(
    page,
    target: str,
    before_lines: list[str] | None = None,
    timeout_ms: float = 30000.0,
) -> str:
    """Confirm a selected navigation marker or newly appeared workspace text."""
    expected = ' '.join(target.split())
    script = """expected => {
      const norm = value => (value || '').replace(/\\s+/g, ' ').trim();
      const visible = element => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
      const root = document.querySelector('#funcPanel') ||
        [...document.querySelectorAll('main,[role="main"],[role="application"],#workspace,.workspace,.frameMain,.frameWork,.frameContent')]
          .filter(visible).sort((left, right) => (right.innerText || '').length - (left.innerText || '').length)[0];
      const lines = root ? (root.innerText || '').split(/\\n/).map(line => norm(line)).filter(Boolean) : [];
      const menu = document.querySelector('#themesCellLimiter');
      const navRoot = menu || document;
      let active = false;
      for (const element of navRoot.querySelectorAll('*')) {
        if (!visible(element) || norm(element.innerText || element.textContent) !== expected) continue;
        for (let node = element; node && node !== navRoot.parentElement; node = node.parentElement) {
          const current = node.getAttribute('aria-current');
          const selected = node.getAttribute('aria-selected') === 'true';
          const classes = typeof node.className === 'string' ? node.className.toLowerCase().split(/\\s+/) : [];
          if (current === 'page' || current === 'true' || selected ||
              classes.some(name => /^(?:is-)?(?:active|selected|current|select)$/.test(name))) {
            active = true;
            break;
          }
        }
        if (active) break;
      }
      return {active, lines};
    }"""
    previous = set(before_lines or [])
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    while True:
        after_lines = []
        for frame in page.frames:
            try:
                state = frame.evaluate(script, expected)
            except Exception:
                continue
            if state['active']:
                return f"Выбор раздела «{target}» подтверждён классом select выбранного пункта навигации"
            after_lines.extend(state['lines'])
        for line in after_lines:
            if line not in previous and expected.casefold() in line.casefold():
                return f"Выбор раздела «{target}» подтверждён новой строкой рабочей области «{line}»"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        page.wait_for_timeout(min(250, max(1, int(remaining * 1000))))
    raise AssertionError(
        f"Раздел «{target}» не подтверждён: активный маркер или новая строка рабочей области не найдены"
    )




class WebClientRunner:
    """Исполнитель сценариев веб-клиента 1С:Предприятие 8.3."""

    SUPPORTED_ACTIONS = frozenset({'click', 'fill', 'assert_text', 'assert_section', 'wait', 'open_url'})

    def __init__(self, profile: Profile, headless: bool = True, default_timeout: float = 30000.0):
        self.profile = profile
        self.headless = headless
        self.default_timeout = default_timeout
        self.web_urls = [url.rstrip('/') for url in container_access_urls(profile.web_url or profile.odata_url)]
        self.web_url = self.web_urls[0]
        # Исходный (не mapped) адрес публикации: из него вычисляются адреса попытки при навигации.
        self.raw_web_url = self.web_urls[-1]
        # Адрес, по которому реально открылась публикация (при откате — исходный loopback).
        self.last_web_url = self.web_url
        # Факты последней попытки входа без значений полей (заполняется в handle_login).
        self.last_login_diagnostics: dict | None = None
        # Снимок формы до отправки: пригодится, если снимок после клика окажется пустым.
        self.last_login_presubmit_shot: bytes = b'' 

    def _checked_navigation_addresses(self, target: str) -> list[str]:
        """Адреса перехода для open_url: mapped и исходный, но только в пределах публикации проекта."""
        base = f"{self.raw_web_url.rstrip('/')}/"
        addresses = container_access_urls(urljoin(base, target or base))
        allowed = {_origin(url) for url in self.web_urls}
        for address in addresses:
            if _origin(address) not in allowed:
                raise ValueError("Отказано в open_url: origin целевого URL не совпадает с origin веб-клиента проекта")
        return addresses


    # Кандидаты в поле логина: только элементы самой формы входа, без угадывания любого text-поля страницы.
    # id формы подтверждены read-only зондом 2026-11-17 (probe-login, boot=true): DemoTrd отдаёт
    # input#authWindow_basic_login, input#authWindow_basic_password и button#authWindow_basic_okButton.
    LOGIN_FIELD_SELECTORS = (
        '#authWindow_basic_login',
        '#authWindow input[type="text"]',
        'input[id*="userName" i]',
        'input[name*="userName" i]',
        'input[name*="user" i]',
        'input[id*="login" i]',
    )
    LOGIN_PASSWORD_SELECTORS = ('#authWindow_basic_password', 'input[type="password"]')
    LOGIN_BUTTON_SELECTORS = ('#authWindow_basic_okButton', 'button[id*="ok"]', 'button:has-text("OK")')
    LOGIN_FORM_HINT = 'input[type="password"], #authWindow, #authWindowBasic'
    WORKSPACE_MARKERS = ('Продажи', 'Главное', 'Закупки', 'Начальная страница', 'Не найдена лицензия',
                         'вход в приложение невозможен', 'Выполнить запуск')
    # Сколько ждём ухода формы логина после одной попытки, прежде чем считать вход неудачным.
    # Настраивается TP_1C_LOGIN_GRACE_SECONDS (2..60): на медленном стенде дефолт 10 с может быть мал.
    LOGIN_GRACE_SECONDS = int_env('TP_1C_LOGIN_GRACE_SECONDS', 30, 2, 60)
    # Сколько ждём появления формы входа (или рабочего стола) после навигации: публикация DemoTrd
    # отдаёт форму примерно через 15 с. Настраивается TP_1C_LOGIN_WAIT_SECONDS (5..120).
    LOGIN_WAIT_SECONDS = int_env('TP_1C_LOGIN_WAIT_SECONDS', 25, 5, 120)
    # Как отправляем форму входа: click — кнопка «Войти», enter — Enter в поле пароля. Одна попытка.
    LOGIN_SUBMIT = choice_env('TP_1C_LOGIN_SUBMIT', 'click', ('click', 'enter'))
    LOGIN_FAILED = ('Вход в 1С не выполнен: форма логина осталась на экране '
                    '(селектор не найден или кнопка «Войти» не сработала)')

    def _login_form_visible(self, page) -> bool:
        """Форма входа ещё на экране: видим поле пароля или окно авторизации."""
        for selector in ('input[type="password"]', '#authWindow', '#authWindowBasic'):
            try:
                if page.locator(selector).first.is_visible(timeout=300):
                    return True
            except Exception:
                continue
        return False

    # Сообщения платформы о неудачном входе/блокировке: показываем их в ошибке шага.
    LOGIN_ERROR_MARKERS = (
        'Неверный пароль',
        'Блокировка',
        'Превышено количество попыток',
        'Пользователь не найден',
        'Неверное имя',
    )

    LOGIN_PROCESSING_SCRIPT = """() => {
        const visible = element => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
        const scope = document.querySelector('#authWindow');
        if (!scope) return false;
        const busy = [...scope.querySelectorAll('[aria-busy="true"], .waitingOverlay, .blockUI')].some(visible);
        if (busy) return true;
        return [...scope.querySelectorAll('input, button')].some(element => element.disabled);
    }"""

    def _login_processing(self, frame) -> bool:
        """Признаки обработки входа: поля/кнопка disabled или aria-busy/спиннер."""
        try:
            return bool(frame.evaluate(self.LOGIN_PROCESSING_SCRIPT))
        except Exception:
            return False

    def _platform_login_error(self, page) -> str | None:
        """Текст ошибки входа из сообщения платформы, если он появился на странице."""
        try:
            body = page.locator('body').inner_text(timeout=3000) or ''
        except Exception:
            return None
        for marker in self.LOGIN_ERROR_MARKERS:
            if marker in body:
                return marker
        return None

    LOGIN_DUMP_SCRIPT = """() => {
        const visible = element => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
        const safe = (value, limit) => (value || '').replace(/[^A-Za-z0-9_.:-]/g, '').slice(0, limit);
        const inputs = [...document.querySelectorAll('input')].filter(visible).slice(0, 6).map(element => ({
            id: safe(element.id, 40),
            name: safe(element.getAttribute('name'), 40),
            type: safe(element.getAttribute('type'), 40),
        }));
        const buttons = [...document.querySelectorAll('button, [role="button"], input[type="submit"]')]
            .filter(visible).slice(0, 3).map(element => ({id: safe(element.id, 40)}));
        const auth = [...document.querySelectorAll('[id^="authWindow"]')].map(element => safe(element.id, 40)).filter(Boolean);
        return {inputs, buttons, auth};
    }"""

    def _login_form_dump(self, page) -> str:
        """Компактное описание видимой формы входа: tag, type и короткие id/name.

        Текст кнопок, placeholder и значения полей не собираются: там бывают пользовательские строки.
        """
        try:
            state = page.evaluate(self.LOGIN_DUMP_SCRIPT) or {}
        except Exception:
            return ''
        parts: list[str] = []
        for item in state.get('inputs') or []:
            attributes = [f'{key}={item[key]}' for key in ('id', 'name', 'type') if item.get(key)]
            parts.append('input[' + ','.join(attributes) + ']')
        for item in state.get('buttons') or []:
            if item.get('id'):
                parts.append(f"button[id={item['id']}]")
        for container in state.get('auth') or []:
            parts.append(f'#{container}')
        return ('форма входа: ' + ', '.join(parts))[:300] if parts else ''

    def _login_failure(self, page, reason: str = '', note: str = '') -> RuntimeError:
        """Ошибка входа с причиной и компактным дампом видимой формы (без значений полей)."""
        dump = self._login_form_dump(page)
        message = f'{self.LOGIN_FAILED}{note}'
        if reason:
            message += f': {reason}'
        if dump:
            message += f'\n{dump}'
        return RuntimeError(message)

    def _login_frame(self, page):
        """Фрейм с формой входа: форма может жить в iframe, а не в верхнем документе."""
        frames = _visible_frames(page)
        for frame in frames:
            try:
                if frame.locator(', '.join(self.LOGIN_FIELD_SELECTORS)).first.is_visible(timeout=300):
                    return frame
            except Exception:
                continue
        for frame in frames:
            try:
                if frame.locator(self.LOGIN_FORM_HINT).first.is_visible(timeout=300):
                    return frame
            except Exception:
                continue
        return None

    def _workspace_visible(self, page) -> bool:
        """Рабочий стол уже открыт: маркеры ищем во всех фреймах, не только в верхнем документе."""
        for frame in _visible_frames(page):
            try:
                # innerText, а не textContent: текст <script> со словами вроде «Продажи» не должен
                # выглядеть как открытый рабочий стол.
                text = frame.evaluate('() => (document.body && document.body.innerText) || ""') or ''
            except Exception:
                continue
            if any(marker in text for marker in self.WORKSPACE_MARKERS):
                return True
        return False

    def _login_frame_error(self, page) -> RuntimeError:
        """Форма не найдена ни в одном фрейме: в ошибке — число фреймов и их origin."""
        frames = _visible_frames(page)
        origins = ', '.join(
            f'#{index} {_origin_text(getattr(frame, "url", "") or "")}' for index, frame in enumerate(frames)
        )
        reason = f'форма входа не найдена ни в одном фрейме (фреймов: {len(frames)}; origin: {origins})'
        return self._login_failure(page, reason=reason)

    def _await_login_state(self, page):
        """Опрос всех фреймов до появления формы входа или рабочего стола.

        Пределы пути входа не суммируются бесконтрольно: ожидание <= LOGIN_WAIT_SECONDS
        (5..120, по умолчанию 25 с), затем ожидание видимого поля <= 3 с, после клика grace
        <= 45 с (внутри — LOGIN_GRACE_SECONDS), финальная рабочая область <= 20 с и 1.5 с
        стабилизации. Худший случай ~ 94.5 с, обычный — секунды.
        """
        deadline = time.monotonic() + self.LOGIN_WAIT_SECONDS
        while True:
            frame = self._login_frame(page)
            if frame is not None:
                return 'form', frame
            if self._workspace_visible(page):
                return 'workspace', None
            if time.monotonic() >= deadline:
                return 'timeout', None
            page.wait_for_timeout(500)

    def _await_login_form_ready(self, frame, user_input) -> None:
        """До 3 с (шаг 100 мс) ждём, что поле логина удержало значение: иначе submit не делаем.

        Счётчик кликов не сбрасывается и не повторяется: одна попытка на прогон.
        """
        expected = (self.profile.username or '').strip()
        deadline = time.monotonic() + 3.0
        while True:
            try:
                value = (user_input.input_value(timeout=500) or '').strip()
            except Exception:
                value = ''
            if value == expected:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('форма не готова к отправке: поле логина не удержало значение')
            frame.wait_for_timeout(100)

    def _await_login_button_ready(self, frame, ok_btn) -> None:
        """Опрос is_enabled() каждые 100 мс до 3 с: is_enabled сам не ждёт активации кнопки."""
        deadline = time.monotonic() + 3.0
        while True:
            try:
                enabled = ok_btn.is_enabled()
            except Exception:
                enabled = False
            if enabled:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('форма не готова к отправке: кнопка недоступна')
            frame.wait_for_timeout(100)

    def handle_login(self, page) -> None:
        """Авторизация в веб-клиенте 1С 8.3/8.5; ровно одна попытка, при неудаче — понятная ошибка."""
        # Раннер один на несколько кейсов: снимок прошлой попытки не должен прилипнуть к текущей.
        self.last_login_presubmit_shot = b''
        # Диагностика для результата прогона: без значений полей, только факты действий.
        self.last_login_diagnostics = {
            'stage': 'wait_form',
            'user_filled': False,
            'password_filled': False,
            'clicked': False,
            'submitted_via': None,
            'form_visible_after': None,
            'after_click_state': None,
        }
        state, frame = self._await_login_state(page)
        self.last_login_diagnostics['stage'] = state
        if state == 'workspace':
            return  # формы входа нет — уже рабочий стол
        if state == 'timeout':
            raise self._login_frame_error(page)

        user_input = frame.locator(', '.join(self.LOGIN_FIELD_SELECTORS)).first
        last_error: Exception | None = None
        try:
            user_input.wait_for(state='visible', timeout=3000)
        except Exception as error:  # ни один селектор не подошёл
            last_error = error
            user_input = None

        if user_input is None:
            self.last_login_diagnostics.update(stage='login_field_missing',
                                               form_visible_after=self._login_form_visible(frame))
            raise self._login_failure(frame, reason=str(last_error) if last_error else '')

        # Пустой пароль легитимен (так настроен демо-стенд): заполняем только непустой, submit не блокируем.
        password_note = '' if self.profile.password else ' (пароль пустой — как настроено в проекте)'
        try:
            if self.profile.username:
                user_input.fill(self.profile.username)
                self.last_login_diagnostics['user_filled'] = True
                self._await_login_form_ready(frame, user_input)
            pwd_input = frame.locator(', '.join(self.LOGIN_PASSWORD_SELECTORS)).first
            if pwd_input.is_visible(timeout=1000) and self.profile.password:
                pwd_input.fill(self.profile.password)
                self.last_login_diagnostics['password_filled'] = True
            if self.LOGIN_SUBMIT == 'enter':
                # Enter в поле пароля (или логина, если пароля нет): тоже ровно одна попытка.
                focus_target = pwd_input if pwd_input.is_visible(timeout=500) else user_input
                # Снимок готовой формы до submit: fallback-evidence, если пост-кликовый снимок пуст.
                try:
                    self.last_login_presubmit_shot = _safe_screenshot(page.context, page) or b''
                except Exception:
                    self.last_login_presubmit_shot = b''
                self.last_login_diagnostics['submitted_via'] = 'enter'
                focus_target.focus()
                focus_target.press('Enter')
            else:
                ok_btn = frame.get_by_role('button', name='Войти').first
                if not ok_btn.is_visible(timeout=500):
                    ok_btn = frame.locator(', '.join(self.LOGIN_BUTTON_SELECTORS)).first
                if not ok_btn.is_visible(timeout=1000):
                    raise RuntimeError('кнопка «Войти» не найдена на форме логина')
                self._await_login_button_ready(frame, ok_btn)
                # Снимок готовой формы до submit: fallback-evidence, если пост-кликовый снимок пуст.
                try:
                    self.last_login_presubmit_shot = _safe_screenshot(page.context, page) or b''
                except Exception:
                    self.last_login_presubmit_shot = b''
                ok_btn.click()  # ровно одна попытка: повторных кликов и ретраев нет
                self.last_login_diagnostics['clicked'] = True
                self.last_login_diagnostics['submitted_via'] = 'click'
        except Exception as error:
            self.last_login_diagnostics.update(stage='submit',
                                               form_visible_after=self._login_form_visible(frame))
            raise self._login_failure(frame, reason=str(error), note=password_note) from error

        # Ждём исхода одной попытки: ошибка платформы, уход формы логина или рабочая область.
        # Состояние «в процессе» (disabled-поля, aria-busy, спиннер) — это ещё не ошибка: ждём до grace.
        started = time.monotonic()
        while time.monotonic() - started < self.LOGIN_GRACE_SECONDS:
            if self._platform_login_error(frame):
                break
            if not self._login_form_visible(frame):
                break
            frame.wait_for_timeout(200)

        form_visible_after = self._login_form_visible(frame)
        platform_error = self._platform_login_error(frame)
        if platform_error:
            after_click_state = 'platform_error'
        elif not form_visible_after:
            after_click_state = 'workspace'
        elif self._login_processing(frame):
            after_click_state = 'processing'
        else:
            after_click_state = 'form_idle'
        self.last_login_diagnostics.update(stage='after_click', form_visible_after=form_visible_after,
                                           after_click_state=after_click_state)
        if platform_error:
            raise self._login_failure(frame, reason=platform_error, note=password_note)
        if form_visible_after:
            raise self._login_failure(frame, reason=f'состояние после клика: {after_click_state}', note=password_note)

        try:
            page.wait_for_function(
                """() => {
                    const txt = document.body.innerText || '';
                    return ['Продажи', 'Главное', 'Закупки', 'Начальная страница', 'Не найдена лицензия',
                        'вход в приложение невозможен', 'Выполнить запуск'].some(text => txt.includes(text));
                }""",
                timeout=20000,
            )
        except Exception:
            pass

        platform_error = self._platform_login_error(frame)
        if platform_error:
            raise self._login_failure(frame, reason=platform_error, note=password_note)
        page.wait_for_timeout(1500)

    def wait_for_1c(self, page) -> None:
        """Ожидание готовности интерфейса 1С:Предприятия."""
        # Ждём исчезновения оверлеев загрузки и появления рабочей области
        try:
            page.wait_for_selector('.waitingOverlay, .blockUI', state='hidden', timeout=2000)
        except Exception:
            pass

    def run_scenario(self, title: str, steps: list[dict], playwright_instance=None) -> ScenarioReport:
        """Выполнение последовательности шагов сценария."""
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        report = ScenarioReport(title=title, status='passed')
        start_time = time.perf_counter()

        def _run(p):
            path = browser_path(p)
            if not path:
                raise RuntimeError("Chromium не найден для запуска веб-клиента 1С")

            browser = p.chromium.launch(executable_path=path, headless=self.headless, args=['--no-sandbox', '--disable-dev-shm-usage'])
            try:
                context = browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    locale='ru-RU',
                    ignore_https_errors=True
                )
                page = context.new_page()
                page.set_default_timeout(self.default_timeout)

                # Шаг 0: Открытие URL веб-клиента
                step_start = time.perf_counter()
                page_url = navigate(page, self.raw_web_url)
                self.last_web_url = page_url
                report.url = page_url
                self.handle_login(page)
                report.pre_submit_screenshot = self.last_login_presubmit_shot or b''
                if report.pre_submit_screenshot:
                    report.steps.append(StepResult(
                        step_number=0, action='login', target='вход: скриншот формы до отправки', value=None,
                        status='passed', message='Форма входа готова к отправке', duration_seconds=0.0,
                        screenshot=report.pre_submit_screenshot,
                    ))
                self.wait_for_1c(page)
                body_txt = page.locator('body').inner_text() or ''
                if 'Не найдена лицензия' in body_txt or 'вход в приложение невозможен' in body_txt:
                    shot = _safe_screenshot(context, page)
                    report.steps.append(StepResult(
                        step_number=0, action='open_url', target=page_url, value=None,
                        status='failed', message='1С отклонила запуск сеанса: ' + body_txt.replace('\n', ' ')[:240],
                        duration_seconds=round(time.perf_counter() - step_start, 3), screenshot=shot
                    ))
                    report.screenshots.append(shot)
                    report.screenshot_stages.append('step_failure')
                    report.status = 'failed'
                    report.error = 'Платформа 1С заблокировала вход в веб-клиент: ' + body_txt.replace('\n', ' ')[:240]
                    return report
                
                shot = _safe_screenshot(context, page)
                report.steps.append(StepResult(
                    step_number=0,
                    action='open_url',
                    target=page_url,
                    value=None,
                    status='passed',
                    message=f'Веб-клиент 1С загружен ({page_url})',
                    duration_seconds=round(time.perf_counter() - step_start, 3),
                    screenshot=shot
                ))
                report.screenshots.append(shot)
                report.screenshot_stages.append('step_ok')

                # Выполнение переданных шагов
                section_baseline = None
                for idx, step in enumerate(steps, 1):
                    s_start = time.perf_counter()
                    action = step.get('action')
                    target = step.get('target', '')
                    val = step.get('value')
                    step_message = 'OK'
                    
                    try:
                        if action not in self.SUPPORTED_ACTIONS:
                            raise ValueError(f"Неизвестное действие шага: {action}")
                        if action == 'open_url':
                            addresses = self._checked_navigation_addresses(target)
                            page_url = navigate(page, addresses[0], addresses=addresses)
                            self.last_web_url = page_url
                            report.url = page_url
                            self.handle_login(page)
                            self.wait_for_1c(page)
                            step_message = f'Веб-клиент 1С загружен ({page_url})'
                        elif action == 'click':
                            section_baseline = _workspace_lines(page)
                            clicked = False
                            for loc in (
                                page.get_by_text(target, exact=True).filter(visible=True).first,
                                page.get_by_text(target, exact=False).filter(visible=True).first,
                            ):
                                try:
                                    loc.wait_for(state='visible', timeout=2000)
                                    loc.click()
                                    clicked = True
                                    break
                                except Exception:
                                    pass
                            if not clicked:
                                page.locator(target).first.click()
                        elif action == 'fill':
                            filled = False
                            for loc in (
                                page.get_by_label(target).first,
                                page.locator(f'input[name*="{target}"], input[id*="{target}"], textarea[name*="{target}"]').first,
                            ):
                                try:
                                    if loc.is_visible(timeout=2000):
                                        loc.fill(str(val))
                                        filled = True
                                        break
                                except Exception:
                                    pass
                            if not filled:
                                page.locator(target).first.fill(str(val))
                        elif action == 'assert_section':
                            step_message = assert_section(page, target, section_baseline, self.default_timeout)
                        elif action == 'assert_text':
                            loc = page.get_by_text(target, exact=True).filter(visible=True).first
                            try:
                                loc.wait_for(state='visible', timeout=3000)
                            except PlaywrightTimeoutError:
                                raise AssertionError(f"Текст '{target}' не обнаружен на форме 1С")
                        elif action == 'wait':
                            page.wait_for_timeout(float(val or 1000))
                        else:
                            raise ValueError(f"Неизвестное действие шага: {action}")

                        self.wait_for_1c(page)
                        s_duration = round(time.perf_counter() - s_start, 3)
                        shot = _safe_screenshot(context, page)
                        report.steps.append(StepResult(
                            step_number=idx,
                            action=action,
                            target=target,
                            value=val,
                            message=step_message,
                            status='passed',
                            duration_seconds=s_duration,
                            screenshot=shot
                        ))
                        report.screenshots.append(shot)
                        report.screenshot_stages.append('step_ok')

                    except Exception as step_exc:
                        s_duration = round(time.perf_counter() - s_start, 3)
                        shot = _safe_screenshot(context, page)
                        report.steps.append(StepResult(
                            step_number=idx,
                            action=action,
                            target=target,
                            value=val,
                            status='failed',
                            message=str(step_exc),
                            duration_seconds=s_duration,
                            screenshot=shot
                        ))
                        report.screenshots.append(shot)
                        report.screenshot_stages.append('step_failure')
                        report.status = 'failed'
                        report.error = f"Ошибка на шаге {idx} ({action} {target}): {step_exc}"
                        break

            except Exception as error:
                # Ошибка до шагов (навигация, вход): снимок делаем до закрытия страницы, иначе evidence теряется.
                report.pre_submit_screenshot = getattr(self, 'last_login_presubmit_shot', b'') or b''
                shot = _safe_screenshot(context, page)
                if shot:
                    report.steps.append(StepResult(
                        step_number=0, action='open_url', target=report.url or self.raw_web_url, value=None,
                        status='failed', message=str(error),
                        duration_seconds=round(time.perf_counter() - start_time, 3), screenshot=shot,
                    ))
                    report.screenshots.append(shot)
                    diagnostics = getattr(self, 'last_login_diagnostics', None) or {}
                    submitted = bool(diagnostics.get('clicked')) or diagnostics.get('submitted_via') == 'enter'
                    report.screenshot_stages.append('post_click' if submitted else 'step_failure')
                raise
            finally:
                try:
                    page.close(run_before_unload=True)
                    time.sleep(0.5)
                except Exception:
                    pass
                browser.close()

        try:
            if playwright_instance:
                _run(playwright_instance)
            else:
                with sync_playwright() as p:
                    _run(p)
        except Exception as e:
            report.status = 'error'
            report.error = str(e)

        report.total_duration_seconds = round(time.perf_counter() - start_time, 3)
        return report


# Read-only зонд формы входа: только чтение разметки, без кликов, fill и ввода данных.
LOGIN_PROBE_SCRIPT = """() => {
    const visible = element => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    const safe = (value, limit) => (value || '').replace(/[^A-Za-z0-9_.:-]/g, '').slice(0, limit);
    const inputs = [...document.querySelectorAll('input')].filter(visible).slice(0, 8).map(element => ({
        tag: 'input',
        type: safe(element.getAttribute('type'), 20),
        id: safe(element.id, 40),
        name: safe(element.getAttribute('name'), 40),
    }));
    const buttons = [...document.querySelectorAll('button, [role="button"], input[type="submit"]')]
        .filter(visible).slice(0, 5).map(element => ({tag: 'button', id: safe(element.id, 40)}));
    const containers = [...document.querySelectorAll('[id^="authWindow"]')]
        .map(element => safe(element.id, 40)).filter(Boolean);
    const passwordVisible = [...document.querySelectorAll('input[type="password"]')].some(visible);
    const authWindowVisible = [...document.querySelectorAll('[id^="authWindow"]')].some(visible);
    return {inputs, buttons, containers, loginFormVisible: passwordVisible || authWindowVisible};
}"""


def collect_login_facts(page, frame=None) -> dict:
    """Собирает факты о форме входа, не трогая поля: без value/placeholder, кликов и fill."""
    source = frame if frame is not None else page
    state = source.evaluate(LOGIN_PROBE_SCRIPT) or {}
    try:
        parts = urlsplit(source.url)
        origin = f'{parts.scheme}://{parts.netloc}' if parts.scheme and parts.netloc else ''
    except Exception:
        origin = ''
    try:
        title = page.title() or ''
    except Exception:
        title = ''
    return {
        'login_form_visible': bool(state.get('loginFormVisible')),
        'fields': state.get('inputs') or [],
        'buttons': state.get('buttons') or [],
        'containers': state.get('containers') or [],
        'title': title,
        'url_origin': origin,
        'frame_url': _url_without_query(getattr(source, 'url', '') or ''),
        'frame_name': getattr(source, 'name', '') or '',
    }


PROBE_READY_SCRIPT = """() => {
    const visible = element => !!(element.offsetWidth || element.offsetHeight);
    const form = [...document.querySelectorAll('input[type="password"], [id^="authWindow"]')].some(visible);
    const txt = (document.body && document.body.textContent) || '';
    const workspace = ['Продажи', 'Главное', 'Закупки', 'Начальная страница'].some(text => txt.includes(text));
    return form || workspace;
}"""


def _origin_text(value: str) -> str:
    parts = urlsplit(value or '')
    return f'{parts.scheme}://{parts.netloc}' if parts.scheme and parts.netloc else ''


def _url_without_query(value: str) -> str:
    parts = urlsplit(value or '')
    if not parts.scheme or not parts.netloc:
        return ''
    return f'{parts.scheme}://{parts.netloc}{parts.path}'


FRAME_DIAGNOSTIC_SCRIPT = """() => {
    const safe = (value, limit) => (value || '').replace(/[^A-Za-z0-9_.:-]/g, '').slice(0, limit);
    return [...document.querySelectorAll('input')].slice(0, 3).map(element => ({
        tag: 'input',
        type: safe(element.getAttribute('type'), 20),
        id: safe(element.id, 40),
        name: safe(element.getAttribute('name'), 40),
    }));
}"""


def _visible_frames(page) -> list:
    """Все фреймы страницы; моки без frames считаются одним документом."""
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    return frames or [page]


def _wait_login_frame(page, timeout_ms: int):
    """Опрос всех фреймов: форма может жить в iframe, а не в верхнем документе."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for frame in _visible_frames(page):
            try:
                ready = frame.evaluate(PROBE_READY_SCRIPT)
            except Exception:
                continue
            if ready:
                return frame
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(500)


def _frame_diagnostics(page) -> str:
    frames = _visible_frames(page)
    parts = []
    for index, frame in enumerate(frames):
        url = _url_without_query(getattr(frame, 'url', '') or '')
        try:
            inputs = frame.evaluate(FRAME_DIAGNOSTIC_SCRIPT)
        except Exception as error:
            inputs = f'ошибка чтения: {error}'
        parts.append(f'#{index} {url} inputs={inputs}')
    return f'форма входа не найдена; фреймов: {len(frames)}; ' + '; '.join(parts)



def _login_probe_open_error(reason) -> RuntimeError:
    return RuntimeError(f'Не удалось открыть веб-клиент проекта: {reason}')


def probe_route_guard(allowed, state, boot: bool = False):
    """Фильтр: чужой origin блокируется всегда; non-GET разрешён только same-origin и только в режиме boot."""
    def guard(route):
        request = route.request
        method = (getattr(request, 'method', 'GET') or 'GET').upper()
        url = getattr(request, 'url', '') or ''
        origin = _origin(url)
        if origin in allowed:
            if method in ('GET', 'HEAD'):
                route.continue_()
                return
            if boot:
                state['non_get_allowed'] = state.get('non_get_allowed', 0) + 1
                route.continue_()
                return
            state['blocked_non_get'] = state.get('blocked_non_get', 0) + 1
            route.abort()
            return
        state['blocked'] += 1
        state['blocked_origins'].append(_origin_text(url))
        if getattr(request, 'resource_type', '') == 'document':
            state['blocked_main'] = True
        route.abort()

    return guard


def _blocked_origin_error(state, web_url: str) -> RuntimeError:
    allowed_text = ', '.join(sorted({_origin_text(url) for url in container_access_urls(web_url)}))
    return RuntimeError(f'Запрос на сторонний origin отклонён: {state["blocked_origins"][0]} (разрешено: {allowed_text})')


PROBE_RETRY_ERRORS = ('ERR_CONNECTION_REFUSED', 'ERR_NAME_NOT_RESOLVED', 'ERR_ADDRESS_UNREACHABLE')


def _is_connection_error(error) -> bool:
    """Переходить к следующему адресу можно только при отказе соединения: не таймаут и не HTTP-статус."""
    text = str(error)
    return any(token in text for token in PROBE_RETRY_ERRORS)


def probe_login_page(page, web_url: str, timeout_ms: int = 20000, boot: bool = False) -> dict:
    """Открывает web_url на переданной странице и возвращает факты; ошибки открытия/origin не скрываются."""
    targets = list(container_access_urls(web_url))
    allowed = {_origin(url) for url in targets}
    state = {'blocked': 0, 'blocked_non_get': 0, 'non_get_allowed': 0, 'blocked_origins': [], 'blocked_main': False}
    if hasattr(page, 'route'):
        page.route('**/*', probe_route_guard(allowed, state, boot))

    tried: list[str] = []
    opened = False
    last_error = None
    for index, target in enumerate(targets):
        tried.append(_origin_text(target))
        state['blocked_main'] = False
        try:
            navigate(page, target)
            opened = True
            break
        except Exception as error:
            if state['blocked_main']:
                raise _blocked_origin_error(state, web_url) from error
            last_error = error
            if index + 1 < len(targets) and _is_connection_error(error):
                continue
            break
    if not opened:
        suffix = f'; испробованы: {", ".join(tried)}' if len(tried) > 1 else ''
        raise _login_probe_open_error(f'{last_error}{suffix}') from last_error

    actual = page.url or ''
    parts = urlsplit(actual)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        if state['blocked_main']:
            raise _blocked_origin_error(state, web_url)
        raise _login_probe_open_error(f'страница не открыта ({actual or "about:blank"})')
    actual_origin = _origin(actual)
    if actual_origin not in allowed:
        raise RuntimeError(f'Страница ушла на другой origin: {_origin_text(actual)} (ожидался {_origin_text(web_url)})')

    frame = _wait_login_frame(page, timeout_ms)
    if frame is None:
        raise _login_probe_open_error(_frame_diagnostics(page))

    page.wait_for_timeout(1000)
    facts = collect_login_facts(page, frame)
    facts['web_url_used'] = _origin_text(actual)
    facts['blocked_requests'] = state['blocked']
    facts['blocked_non_get'] = state['blocked_non_get']
    facts['non_get_requests'] = state['non_get_allowed']
    facts['mode'] = 'boot' if boot else 'get'
    facts['credentials_sent'] = False
    return facts


def probe_login_form(web_url: str, timeout_ms: int = 20000, boot: bool = False) -> dict:
    """Открывает web_url, ждёт форму входа или рабочий стол и возвращает только факты разметки."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        path = browser_path(p)
        if not path:
            raise _login_probe_open_error('Chromium не найден')
        browser = p.chromium.launch(executable_path=path, headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            context = browser.new_context(viewport={'width': 1280, 'height': 800})
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            return probe_login_page(page, web_url, timeout_ms, boot)
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()
