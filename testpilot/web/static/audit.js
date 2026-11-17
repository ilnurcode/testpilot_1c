'use strict';

async function auditPage() {
  heading('Аудит BSL и стандарты v8std');
  $('#content').innerHTML = `
  <div class="page-heading">
    <div>
      <div class="eyebrow">СТАТИЧЕСКИЙ АНАЛИЗ И БЕЗОПАСНОСТЬ 1С</div>
      <h1>Аудит BSL и стандарты v8std</h1>
      <p class="page-description">Поиск уязвимостей (RCE в Выполнить, инъекций в Запрос.Текст, дедлоков) и проверка соответствия стандартам разработки 1С (v8std / ИТС).</p>
    </div>
  </div>
  <div class="card">
    <div class="row">
      <button id="modeCode" class="secondary">Ввод BSL кода</button>
      <button id="modePath" class="secondary">Каталог / Расширение</button>
    </div>
    <div id="boxCode" style="margin-top: 12px;">
      <label>Исходный код модуля BSL:<textarea id="auditCode" class="prose" rows="12" placeholder="Вставьте код модуля 1С для анализа..."></textarea></label>
    </div>
    <div id="boxPath" style="margin-top: 12px; display: none;">
      <label>Путь к файлу BSL или каталогу выгрузки расширения:<input id="auditPath" placeholder="Например: samples/demo_vulnerable_extension"></label>
    </div>
    <div class="actions" style="margin-top: 14px;">
      <button id="runAuditBtn">🔍 Запустить аудит v8std</button>
    </div>
    <p class="caption" style="margin-top: 10px;">Полный исходник не сохраняется: в журнал попадают сводка и замечания с фрагментами строк.</p>
  </div>
  <div id="auditJournal"></div>
  <div id="auditResult"></div>
  `;

  let mode = 'code';
  const modeCodeBtn = $('#modeCode');
  const modePathBtn = $('#modePath');
  const boxCode = $('#boxCode');
  const boxPath = $('#boxPath');

  modeCodeBtn.onclick = () => {
    mode = 'code';
    modeCodeBtn.className = '';
    modePathBtn.className = 'secondary';
    boxCode.style.display = 'block';
    boxPath.style.display = 'none';
  };

  modePathBtn.onclick = () => {
    mode = 'path';
    modePathBtn.className = '';
    modeCodeBtn.className = 'secondary';
    boxPath.style.display = 'block';
    boxCode.style.display = 'none';
  };


  $('#runAuditBtn').onclick = () => task(async () => {
    const payload = mode === 'code' 
      ? { code: $('#auditCode').value }
      : { path: $('#auditPath').value };

    if (mode === 'code' && !payload.code.trim()) {
      throw Error('Вставьте код BSL для проверки.');
    }
    if (mode === 'path' && !payload.path.trim()) {
      throw Error('Укажите путь к каталогу или файлу.');
    }

    const res = await request('/api/v1/audit/scan', 'POST', payload);
    renderAuditResult(res);
    await refreshAuditJournal();
  });

  await refreshAuditJournal();
}

async function refreshAuditJournal() {
  const host = $('#auditJournal');
  if (!host) return;
  const rows = await request('/api/v1/audit/scans?limit=1');
  if (!rows.length) {
    host.innerHTML = `
    <div class="card" style="margin-top: 16px;">
      <div class="history-header"><h2>Журнал аудитов</h2></div>
      <p class="caption">Сохранённых аудитов пока нет: запустите проверку — запись появится здесь.</p>
    </div>`;
    return;
  }
  const latest = await request(`/api/v1/audit/scans/${encodeURIComponent(rows[0].scan_id)}`);
  host.innerHTML = '<div id="auditJournalCard"></div>';
  renderAuditResultInto(latest, '#auditJournalCard', { title: 'Последний сохранённый аудит', scroll: false });
}

function renderAuditResult(summary) {
  return renderAuditResultInto(summary, '#auditResult');
}

const AUDIT_PAGE_SIZE = 100;
// Поколение рендера контейнера: ответы прошлого рендера не дописывают карточки в новый список.
const auditRenderTokens = new Map();

function auditIssueCard(iss) {
  const isCrit = iss.severity === 'CRITICAL';
  const isHigh = iss.severity === 'HIGH';
  const badgeCls = isCrit ? 'error' : isHigh ? 'warning' : 'pending';
  return `
    <div class="card audit-issue" style="border-left: 4px solid ${isCrit ? '#d32f2f' : isHigh ? '#ed6c02' : '#8b949e'}; padding: 12px;">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
        <span class="badge ${badgeCls}">${E(iss.severity)}</span>
        <strong>${E(iss.rule_id)}</strong>
        <span class="caption">(${E(iss.category)})</span>
      </div>
      <p style="font-weight: 500;">${E(iss.message)}</p>
      <p class="caption" style="font-family: monospace;">Файл: ${E(iss.file_path)} : строка ${E(iss.line)}</p>
      ${iss.code_fragment ? `<pre style="margin: 6px 0; background: rgba(0,0,0,0.05); padding: 6px; border-radius: 4px; overflow-x: auto;"><code>${E(iss.code_fragment)}</code></pre>` : ''}
      ${iss.recommendation ? `<p class="helper" style="margin-top: 4px;"><strong>Стандарт v8std:</strong> ${E(iss.recommendation)}</p>` : ''}
    </div>`;
}

async function renderAuditResultInto(summary, selector, options = {}) {
  const target = $(selector);
  if (!target) return;
  const token = (auditRenderTokens.get(selector) || 0) + 1;
  auditRenderTokens.set(selector, token);

  const passed = summary.passed;
  const nothingChecked = (summary.status || 'scanned') !== 'scanned';
  const sev = summary.issues_by_severity || {};
  const critical = sev.CRITICAL || 0;
  const high = sev.HIGH || 0;
  const total = Number(summary.total_issues || 0);

  const stats = [];
  if (Number.isFinite(summary.total_files)) stats.push(`<div><strong>${summary.total_files}</strong>проверено файлов</div>`);
  stats.push(`<div><strong>${total}</strong>замечаний</div>`);
  stats.push(`<div><strong style="color: ${critical ? '#d32f2f' : 'inherit'}">${critical}</strong>критичных</div>`);
  stats.push(`<div><strong style="color: ${high ? '#ed6c02' : 'inherit'}">${high}</strong>высоких</div>`);

  const facts = [];
  if (typeof summary.duration_ms === 'number') facts.push(`Время анализа: ${Math.round(summary.duration_ms)} мс`);
  if (summary.created_at) facts.push(`Сохранено: ${E(summary.created_at)}`);
  if (summary.sha256) facts.push(`SHA-256: ${E(String(summary.sha256).slice(0, 12))}…`);
  if (summary.source_name) facts.push(`Источник: ${E(summary.source_name)}`);

  const reportLink = summary.scan_id
    ? `<a class="secondary" href="/api/v1/audit/scans/${encodeURIComponent(summary.scan_id)}/report" download>⬇ Скачать отчёт (md)</a>`
    : '';

  let html = `
  <div class="card" style="margin-top: 16px;">
    <div class="history-header">
      <h2>${E(options.title || 'Результаты анализа')}</h2>
      <span style="display: flex; align-items: center; gap: 10px;">
        ${reportLink}
        <span class="badge ${nothingChecked ? 'pending' : passed ? 'ready' : 'pending'}">${nothingChecked ? 'НЕЧЕГО ПРОВЕРЯТЬ' : passed ? 'ПРОЙДЕНО' : 'НАЙДЕНЫ ДЕФЕКТЫ'}</span>
      </span>
    </div>
    <div class="stats">${stats.join('')}</div>
    ${facts.length ? `<p class="caption">${facts.join(' · ')}</p>` : ''}
  `;

  let pager = null;
  if (nothingChecked) {
    html += `<p class="warning" style="margin-top: 12px;">${E(summary.message || 'Исходники для проверки не найдены: аудит не выполнялся.')}</p>`;
  } else if (!total) {
    html += '<p class="success" style="margin-top: 12px;">✓ Уязвимостей и нарушений стандартов v8std не обнаружено.</p>';
  } else {
    html += `
      <div data-audit-list style="display: flex; flex-direction: column; gap: 10px; margin-top: 16px;"></div>
      <p class="caption" data-audit-counter style="margin-top: 10px;"></p>
      <p class="helper" data-audit-limit style="margin-top: 4px; display: none;"></p>
      <div class="actions" style="margin-top: 10px;"><button class="secondary" data-audit-more style="display: none;"></button></div>`;
    pager = { summary, total };
  }

  html += '</div>';
  target.innerHTML = html;
  if (options.scroll !== false) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (pager) await fillAuditIssues(target, pager.summary, pager.total, selector, token);
}

async function fillAuditIssues(target, summary, total, selector, token) {
  const list = target.querySelector('[data-audit-list]');
  const counter = target.querySelector('[data-audit-counter]');
  const limitNote = target.querySelector('[data-audit-limit]');
  const more = target.querySelector('[data-audit-more]');
  const url = summary.issues_url || '';
  const kept = Number(summary.issues_kept ?? (summary.issues || []).length ?? total);
  const truncated = Boolean(summary.issues_truncated);
  // Детали есть только в пределах среза: за его границей запросы не отправляются и кнопка не показывается.
  const available = truncated ? Math.min(kept, total) : total;
  const state = { url, items: url ? [] : (summary.issues || []).slice(0, AUDIT_PAGE_SIZE) };
  const stale = () => auditRenderTokens.get(selector) !== token;

  if (truncated) {
    limitNote.textContent = `Детализация в хранилище ограничена ${available} замечаниями; полное число (${total}) — в сводке.`;
    limitNote.style.display = '';
  }

  const paint = () => {
    list.innerHTML = state.items.map(auditIssueCard).join('');
    const shown = state.items.length;
    counter.textContent = truncated
      ? `показано ${shown} из ${available} сохранённых (всего замечаний ${total})`
      : `показано ${shown} из ${total}`;
    const next = Math.min(AUDIT_PAGE_SIZE, available - shown);
    more.style.display = next > 0 ? '' : 'none';
    if (next > 0) more.textContent = `Показать ещё ${next}`;
  };

  const loadNext = async () => {
    if (state.items.length >= available) return;
    if (state.url) {
      const page = await request(`${state.url}?offset=${state.items.length}&limit=${AUDIT_PAGE_SIZE}`);
      if (stale()) return;
      state.items = state.items.concat((page.items || []).slice(0, available - state.items.length));
    } else {
      const slice = (summary.issues || []).slice(state.items.length, state.items.length + AUDIT_PAGE_SIZE);
      state.items = state.items.concat(slice.slice(0, available - state.items.length));
    }
    paint();
  };

  if (state.url) await loadNext(); else paint();
  if (stale()) return;
  more.onclick = () => task(async () => {
    more.disabled = true;
    try { await loadNext(); } finally { more.disabled = false; }
  });
}
