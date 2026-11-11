import base64
"""Playwright-адаптер для автоматизации сценариев в веб-клиенте 1С:Предприятие."""
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from testpilot.ai.executor import browser_path
from testpilot.integration.probe import Profile

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


def _safe_screenshot(context, page) -> bytes:
    try:
        cdp = context.new_cdp_session(page)
        res = cdp.send('Page.captureScreenshot', {'format': 'png'})
        cdp.detach()
        return base64.b64decode(res['data'])
    except Exception:
        return page.screenshot(full_page=False, animations='disabled', timeout=15000)

class WebClientRunner:
    """Исполнитель сценариев в веб-клиенте 1С:Предприятие 8.3."""

    def __init__(self, profile: Profile, headless: bool = True, default_timeout: float = 30000.0):
        self.profile = profile
        self.headless = headless
        self.default_timeout = default_timeout
        self.web_url = (profile.web_url or profile.odata_url).rstrip('/')

    def handle_login(self, page) -> None:
        """Обработка стандартного окна авторизации веб-клиента 1С (8.3 и 8.5)."""
        try:
            # Платформа 8.5 (#authWindow_basic_login) или 8.3 (#userName)
            user_input = page.locator('#authWindow_basic_login, input[id*="userName"], input[name*="userName"], #userName').first
            try:
                user_input.wait_for(state='visible', timeout=12000)
                has_login = True
            except Exception:
                has_login = False
            if has_login:
                if self.profile.username:
                    user_input.fill(self.profile.username)
                pwd_input = page.locator('#authWindow_basic_password, input[type="password"]').first
                if pwd_input.is_visible(timeout=1000) and self.profile.password:
                    pwd_input.fill(self.profile.password)
                ok_btn = page.get_by_role("button", name="Войти").first
                if not ok_btn.is_visible(timeout=500):
                    ok_btn = page.locator('button[id*="ok"], button:has-text("OK")').first
                if ok_btn.is_visible(timeout=1000):
                    ok_btn.click()
                try:
                    page.wait_for_function(
                        '''() => {
                            const txt = document.body.innerText || '';
                            return txt.includes('Продажи') || txt.includes('Главное') || txt.includes('Закупки') || txt.includes('Начальная страница') || txt.includes('Не найдена лицензия') || txt.includes('вход в приложение невозможен') || txt.includes('Выполнить запуск');
                        }''',
                        timeout=45000
                    )
                except Exception:
                    pass
                page.wait_for_timeout(1500)
        except Exception:
            pass

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
                page.goto(self.web_url, wait_until='domcontentloaded', timeout=45000)
                self.handle_login(page)
                self.wait_for_1c(page)
                body_txt = page.locator('body').inner_text() or ''
                if 'Не найдена лицензия' in body_txt or 'вход в приложение невозможен' in body_txt:
                    shot = _safe_screenshot(context, page)
                    report.steps.append(StepResult(
                        step_number=0, action='open_url', target=self.web_url, value=None,
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
                    target=self.web_url,
                    value=None,
                    status='passed',
                    message='Веб-клиент 1С загружен',
                    duration_seconds=round(time.perf_counter() - step_start, 3),
                    screenshot=shot
                ))
                report.screenshots.append(shot)

                # Выполнение переданных шагов
                for idx, step in enumerate(steps, 1):
                    s_start = time.perf_counter()
                    action = step.get('action')
                    target = step.get('target', '')
                    val = step.get('value')
                    
                    try:
                        if action == 'click':
                            clicked = False
                            for loc in (
                                page.get_by_text(target, exact=True).first,
                                page.get_by_text(target, exact=False).first,
                            ):
                                try:
                                    if loc.is_visible(timeout=2000):
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
                        elif action == 'assert_text':
                            loc = page.get_by_text(target).first
                            if not loc.is_visible(timeout=3000):
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
                            status='passed',
                            message='OK',
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
