import base64
"""Playwright-адаптер для автоматизации сценариев в веб-клиенте 1С:Предприятие."""
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from testpilot.ai.executor import browser_path
from testpilot.config import int_env
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
    LOGIN_FIELD_SELECTORS = (
        '#authWindow_basic_login',
        '#authWindow input[type="text"]',
        'input[id*="userName" i]',
        'input[name*="userName" i]',
        'input[name*="user" i]',
        'input[id*="login" i]',
    )
    LOGIN_FORM_HINT = 'input[type="password"], #authWindow, #authWindowBasic'
    # Сколько ждём ухода формы логина после одной попытки, прежде чем считать вход неудачным.
    # Настраивается TP_1C_LOGIN_GRACE_SECONDS (2..60): на медленном стенде дефолт 10 с может быть мал.
    LOGIN_GRACE_SECONDS = int_env('TP_1C_LOGIN_GRACE_SECONDS', 10, 2, 60)
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

    def handle_login(self, page) -> None:
        """Авторизация в веб-клиенте 1С 8.3/8.5; ровно одна попытка, при неудаче — понятная ошибка."""
        user_input = page.locator(', '.join(self.LOGIN_FIELD_SELECTORS)).first
        last_error: Exception | None = None
        try:
            user_input.wait_for(state='visible', timeout=3000)
        except Exception as error:  # ни один селектор не подошёл
            last_error = error
            user_input = None

        if user_input is None:
            if self._login_form_visible(page):
                raise self._login_failure(page, reason=str(last_error) if last_error else '')
            return  # формы входа нет — уже рабочий стол

        # Пустой пароль легитимен (так настроен демо-стенд): заполняем только непустой, submit не блокируем.
        password_note = '' if self.profile.password else ' (пароль пустой — как настроено в проекте)'
        try:
            if self.profile.username:
                user_input.fill(self.profile.username)
                if not (user_input.input_value(timeout=2000) or '').strip():
                    raise RuntimeError('поле логина осталось пустым после заполнения')
            pwd_input = page.locator('#authWindow_basic_password, input[type="password"]').first
            if pwd_input.is_visible(timeout=1000) and self.profile.password:
                pwd_input.fill(self.profile.password)
            ok_btn = page.get_by_role('button', name='Войти').first
            if not ok_btn.is_visible(timeout=500):
                ok_btn = page.locator('button[id*="ok"], button:has-text("OK")').first
            if not ok_btn.is_visible(timeout=1000):
                raise RuntimeError('кнопка «Войти» не найдена на форме логина')
            ok_btn.click()  # ровно одна попытка: повторных кликов и ретраев нет
        except Exception as error:
            raise self._login_failure(page, reason=str(error), note=password_note) from error

        # Ждём исхода одной попытки: ошибка платформы, уход формы логина или рабочая область.
        started = time.monotonic()
        while time.monotonic() - started < 45.0:
            if self._platform_login_error(page):
                break
            if not self._login_form_visible(page):
                break
            if time.monotonic() - started >= self.LOGIN_GRACE_SECONDS:
                break  # форма не ушла за отведённое время — считаем вход неудачным
            page.wait_for_timeout(200)

        platform_error = self._platform_login_error(page)
        if platform_error:
            raise self._login_failure(page, reason=platform_error, note=password_note)
        if self._login_form_visible(page):
            raise self._login_failure(page, note=password_note)  # форма осталась — без ожидания 45 с

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

        platform_error = self._platform_login_error(page)
        if platform_error:
            raise self._login_failure(page, reason=platform_error, note=password_note)
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
                        report.status = 'failed'
                        report.error = f"Ошибка на шаге {idx} ({action} {target}): {step_exc}"
                        break

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


def collect_login_facts(page) -> dict:
    """Собирает факты о форме входа, не трогая поля: без value/placeholder, кликов и fill."""
    state = page.evaluate(LOGIN_PROBE_SCRIPT) or {}
    try:
        parts = urlsplit(page.url)
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


def _login_probe_open_error(reason) -> RuntimeError:
    return RuntimeError(f'Не удалось открыть веб-клиент проекта: {reason}')


def probe_route_guard(allowed, state):
    """Прерывный фильтр: POST/PUT/... отклоняется всегда, GET/HEAD - только в пределах allowlist."""
    def guard(route):
        request = route.request
        method = (getattr(request, 'method', 'GET') or 'GET').upper()
        url = getattr(request, 'url', '') or ''
        origin = _origin(url)
        if method not in ('GET', 'HEAD'):
            state['blocked_non_get'] += 1
            route.abort()
            return
        if origin in allowed:
            route.continue_()
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


def probe_login_page(page, web_url: str, timeout_ms: int = 20000) -> dict:
    """Открывает web_url на переданной странице и возвращает факты; ошибки открытия/origin не скрываются."""
    targets = list(container_access_urls(web_url))
    allowed = {_origin(url) for url in targets}
    state = {'blocked': 0, 'blocked_non_get': 0, 'blocked_origins': [], 'blocked_main': False}
    if hasattr(page, 'route'):
        page.route('**/*', probe_route_guard(allowed, state))

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

    try:
        page.wait_for_function(PROBE_READY_SCRIPT, timeout=timeout_ms)
    except Exception as error:
        raise _login_probe_open_error(f'не дождались формы входа или рабочего стола: {error}') from error

    page.wait_for_timeout(1000)
    facts = collect_login_facts(page)
    facts['web_url_used'] = _origin_text(actual)
    facts['blocked_requests'] = state['blocked']
    facts['blocked_non_get'] = state['blocked_non_get']
    return facts


def probe_login_form(web_url: str, timeout_ms: int = 20000) -> dict:
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
            return probe_login_page(page, web_url, timeout_ms)
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()
