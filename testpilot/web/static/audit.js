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
  </div>
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
  });
}

function renderAuditResult(summary) {
  return renderAuditResultInto(summary, '#auditResult');
}

function renderAuditResultInto(summary, selector) {
  const target = $(selector);
  if (!target) return;

  const passed = summary.passed;
  const nothingChecked = (summary.status || 'scanned') !== 'scanned';
  const sev = summary.issues_by_severity || {};
  const critical = sev.CRITICAL || 0;
  const high = sev.HIGH || 0;
  const medium = sev.MEDIUM || 0;
  const low = sev.LOW || 0;

  let html = `
  <div class="card" style="margin-top: 16px;">
    <div class="history-header">
      <h2>Результаты анализа</h2>
      <span class="badge ${nothingChecked ? 'pending' : passed ? 'ready' : 'pending'}">${nothingChecked ? 'НЕЧЕГО ПРОВЕРЯТЬ' : passed ? 'ПРОЙДЕНО' : 'НАЙДЕНЫ ДЕФЕКТЫ'}</span>
    </div>
    <div class="stats">
      <div><strong>${summary.total_files || 0}</strong>проверено файлов</div>
      <div><strong>${summary.total_issues || 0}</strong>замечаний</div>
      <div><strong style="color: ${critical ? '#d32f2f' : 'inherit'}">${critical}</strong>критичных</div>
      <div><strong style="color: ${high ? '#ed6c02' : 'inherit'}">${high}</strong>высоких</div>
    </div>
    <p class="caption">Время анализа: ${summary.duration_ms} мс</p>
  `;

  if (nothingChecked) {
    html += `<p class="warning" style="margin-top: 12px;">${E(summary.message || 'Исходники для проверки не найдены: аудит не выполнялся.')}</p>`;
  } else if (!summary.issues || summary.issues.length === 0) {
    html += '<p class="success" style="margin-top: 12px;">✓ Уязвимостей и нарушений стандартов v8std не обнаружено.</p>';
  } else {
    html += '<div style="display: flex; flex-direction: column; gap: 10px; margin-top: 16px;">';
    summary.issues.forEach((iss, idx) => {
      const isCrit = iss.severity === 'CRITICAL';
      const isHigh = iss.severity === 'HIGH';
      const badgeCls = isCrit ? 'error' : isHigh ? 'warning' : 'pending';
      html += `
        <div class="card" style="border-left: 4px solid ${isCrit ? '#d32f2f' : isHigh ? '#ed6c02' : '#8b949e'}; padding: 12px;">
          <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
            <span class="badge ${badgeCls}">${E(iss.severity)}</span>
            <strong>${E(iss.rule_id)}</strong>
            <span class="caption">(${E(iss.category)})</span>
          </div>
          <p style="font-weight: 500;">${E(iss.message)}</p>
          <p class="caption" style="font-family: monospace;">Файл: ${E(iss.file_path)} : строка ${iss.line}</p>
          ${iss.code_fragment ? `<pre style="margin: 6px 0; background: rgba(0,0,0,0.05); padding: 6px; border-radius: 4px; overflow-x: auto;"><code>${E(iss.code_fragment)}</code></pre>` : ''}
          ${iss.recommendation ? `<p class="helper" style="margin-top: 4px;"><strong>Стандарт v8std:</strong> ${E(iss.recommendation)}</p>` : ''}
        </div>
      `;
    });
    html += '</div>';
  }

  html += '</div>';
  target.innerHTML = html;
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
