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

class WebClientRunner:
    """Исполнитель сценариев в веб-клиенте 1С:Предприятие 8.3."""

    def __init__(self, profile: Profile, headless: bool = True, default_timeout: float = 15000.0):
        self.profile = profile
        self.headless = headless
        self.default_timeout = default_timeout
        self.web_url = (profile.web_url or profile.odata_url).rstrip('/')

    def handle_login(self, page) -> None:
        """Обработка стандартного окна авторизации веб-клиента 1С."""
        # 1C Web-клиент часто показывает форму входа с выбором пользователя или полями ввода
        try:
            user_input = page.locator('input[id*="userName"], input[name*="userName"], #userName')
            if user_input.is_visible(timeout=2000):
                if self.profile.username:
                    user_input.fill(self.profile.username)
                pwd_input = page.locator('input[type="password"]')
                if pwd_input.is_visible() and self.profile.password:
                    pwd_input.fill(self.profile.password)
                ok_btn = page.locator('button[id*="ok"], button:has-text("Войти"), button:has-text("OK")')
                if ok_btn.is_visible():
                    ok_btn.click()
                page.wait_for_load_state('networkidle', timeout=self.default_timeout)
        except Exception:
            pass

    def wait_for_1c(self, page) -> None:
        """Ожидание готовности интерфейса 1С:Предприятия."""
        # Ждём исчезновения оверлеев загрузки и появления рабочей области
        try:
            page.wait_for_selector('.waitingOverlay, .blockUI', state='detached', timeout=self.default_timeout)
        except Exception:
            pass

    def run_scenario(self, title: str, steps: list[dict], playwright_instance=None) -> ScenarioReport:
        """Выполнение последовательности шагов сценария."""
        from playwright.sync_api import sync_playwright

        report = ScenarioReport(title=title, status='passed')
        start_time = time.perf_counter()

        def _run(p):
            path = browser_path()
            if not path:
                raise RuntimeError("Chromium не найден для запуска веб-клиента 1С")

            browser = p.chromium.launch(executable_path=path, headless=self.headless, args=['--no-sandbox'])
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
                page.goto(self.web_url)
                self.handle_login(page)
                self.wait_for_1c(page)
                
                shot = page.screenshot(full_page=False)
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
                            # Поиск по тексту или селектору
                            loc = page.locator(f'button:has-text("{target}"), a:has-text("{target}"), {target}').first
                            loc.click()
                        elif action == 'fill':
                            loc = page.locator(f'input[name*="{target}"], input[id*="{target}"], {target}').first
                            loc.fill(str(val))
                        elif action == 'assert_text':
                            loc = page.locator(f':has-text("{target}")')
                            if not loc.is_visible():
                                raise AssertionError(f"Текст '{target}' не обнаружен на форме 1С")
                        elif action == 'wait':
                            page.wait_for_timeout(float(val or 1000))
                        else:
                            raise ValueError(f"Неизвестное действие шага: {action}")

                        self.wait_for_1c(page)
                        s_duration = round(time.perf_counter() - s_start, 3)
                        shot = page.screenshot(full_page=False)
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
                        shot = page.screenshot(full_page=False)
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
