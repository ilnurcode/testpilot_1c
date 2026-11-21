'use strict';
let activeSuite='';
let llmSaveState=null;
const stateNames={draft:'Ожидает проверки',approved:'Подтверждён',queued:'В очереди',running:'Выполняется',completed:'Выполнение завершено',error:'Ошибка прогона',cancelled:'Отменён',interrupted:'Прерван',failed:'Расхождение'};
const resultNames={passed:'Проверка пройдена',failed:'Расхождение',error:'Ошибка выполнения',skipped:'Не выполнено',cancelled:'Отменён',interrupted:'Прерван'};
const scopeNames={business:'Бизнес-требование',connectivity:'Доступность / навигация',metadata:'Метаданные',manual:'Ручная проверка'};
const pendingSuite=s=>s.status==='queued'||s.status==='running';
const demoSuite=s=>Boolean(s&&s.synthetic&&s.configuration_id==='presentation-sales');
const valueText=v=>v===undefined?'—':JSON.stringify(v);
const businessText=v=>v===true?'Бизнес-результат подтверждён':v===false?'Бизнес-результат не подтверждён':'Бизнес-результат не проверен / не доказан';
let suitePoll=null;
function stopSuitePolling(){
 if(!suitePoll)return;
 clearTimeout(suitePoll.timer);suitePoll.controller.abort();suitePoll.observer.disconnect();suitePoll=null;
}
window.addEventListener('pagehide',stopSuitePolling);
window.addEventListener('hashchange',()=>{if(suitePoll&&location.hash!=='#ai/'+suitePoll.id)stopSuitePolling()});
function startSuitePolling(s){
 if(!pendingSuite(s))return;
 const host=$('#suiteView'),watch={id:s.suite_id,host,controller:new AbortController(),timer:null,observer:null};
 const live=()=>suitePoll===watch&&host.isConnected&&activeSuite===watch.id&&location.hash==='#ai/'+watch.id;
 watch.observer=new MutationObserver(()=>{if(!host.isConnected&&suitePoll===watch)stopSuitePolling()});
 watch.observer.observe($('#content'),{childList:true});
 suitePoll=watch;
 const get=async suffix=>{
  const response=await fetch('/gateway/intelligence/api/v1/ai/suites/'+encodeURIComponent(watch.id)+suffix,{signal:watch.controller.signal});
  const data=await response.json();
  if(!response.ok)throw Error(response.status===401?'Сеанс завершён. Войдите заново.':responseError(data));
  return data;
 };
 const poll=async()=>{
  if(!live()){if(suitePoll===watch)stopSuitePolling();return}
  if(busyDepth){watch.timer=setTimeout(poll,1000);return}
  try{
   const state=await get('/status');
   if(!live())return;
   // /status — лёгкий poll-only ответ; полный результат/evidence нужен на terminal.
   const full=pendingSuite(state)?s:await get('');
   if(!live())return;
   if(busyDepth){watch.timer=setTimeout(poll,1000);return}
   await renderSuite({...full,...state},false);
  }catch(error){
   if(!live()||error.name==='AbortError')return;
   stopSuitePolling();
   const feedback=$('#suiteFeedback');
   feedback.innerHTML='<p class="warning">Не удалось обновить прогон. Его состояние неизвестно; это не означает завершение. '+E(error.message)+'</p><div id="suiteRetry"></div>';
   addButton('#suiteRetry','Обновить состояние',async()=>{await renderSuite(await api('intelligence','ai/suites/'+encodeURIComponent(watch.id)))},true);
  }
 };
 watch.timer=setTimeout(poll,1000);
}
async function enqueueSuite(s,path){
 const state=await api('intelligence',`ai/suites/${encodeURIComponent(s.suite_id)}/${path}?allow_mutations=${allowMutations()}&strict_read_only=${strictReadOnly()}`,'POST');
 // run/rerun возвращают HTTP202 state, не полный план; rerun имеет новый suite_id.
 activeSuite=state.suite_id;history.replaceState(null,'','#ai/'+state.suite_id);
 await renderSuite({...await api('intelligence','ai/suites/'+encodeURIComponent(state.suite_id)),...state});
}
async function runDemoSuite(s){
 const host=$('#suiteFeedback');
 if(host)host.innerHTML='<p class="info-note">Учебный прогон: HTTP-запросы и шаги браузера к синтетическому приложению. Это не реальная 1С; статусы не назначаются заранее. Прогон не прерывается, дождитесь ответа.</p>';
 let response,data;
 try{
  response=await fetch('/gateway/intelligence/api/v1/ai/suites/'+encodeURIComponent(s.suite_id)+'/run-demo',{method:'POST'});
  data=await response.json().catch(()=>null);
 }catch{
  if(host)host.innerHTML='<p class="warning">Не удалось запустить учебный прогон: сервер недоступен. Состояние плана неизвестно — обновите страницу.</p>';
  return;
 }
 if(!response.ok){
  const detail=responseError(data);
  const text=response.status===409?detail:response.status===403?'Запустить учебный прогон может только роль «Эксперт». '+detail:`Не удалось запустить учебный прогон (HTTP ${response.status}). ${detail}`;
  if(host)host.innerHTML=`<p class="warning">${E(text)}</p>`;
  return;
 }
 activeSuite=data.suite_id;history.replaceState(null,'','#ai/'+data.suite_id);
 await renderSuite(data);
}
function assertionRows(assertions,planned=false){
 return table(['ID','Тип проверки','Статус','Ожидалось (тип / значение)','Получено (тип / значение)','Основание / сообщение'],assertions.map(a=>[
  E(a.assertion_id||a.id),E(scopeNames[a.scope]||a.scope),E(planned?'Запланировано':resultNames[a.status]||a.status),
  E(valueText(a.expected)),planned?'—':E(valueText(a.actual)),E(planned?(a.requirement||a.source_ref||a.check):a.message)
 ]));
}
function proposalCard(p,i,s,selected){
 return `<article class="proposal-card"><div class="history-header"><label class="inline"><input type="checkbox" class="proposal-choice" value="${i}" ${selected.includes(i)?'checked':''} ${s.status!=='draft'?'disabled':''}><strong>${E(p.title)}</strong></label><span class="badge blue">${E(p.engine==='playwright'?'Playwright':p.engine==='odata'?'OData 1С':'Ручная проверка')}</span>${p.mutating?'<span class="badge warning">⚠ изменяет данные (эвристика)</span>':''}</div><p class="caption">${E(p.rationale)}</p><p class="helper">Объекты: ${(p.object_ids||[]).map(E).join(', ')}</p><ol>${(p.steps||[]).map(x=>`<li>${E(x)}</li>`).join('')}</ol><p class="caption"><b>Ожидается:</b> ${E(p.expected_result)}</p>${(p.cases||[]).map((c,j)=>`<details><summary>Набор ${j+1} · ${E(c.entity||'Веб-клиент / ручная проверка')}</summary>${json(c)}${(c.assertions||[]).length?assertionRows(c.assertions,true):'<p class="warning">Typed assertions не заданы; бизнес-результат не доказан.</p>'}</details>`).join('')}</article>`;
}
function resultCard(r){
 const status=Object.hasOwn(resultNames,r.status)?r.status:'skipped';
 return `<article class="result-card"><div class="history-header"><h3>${E(r.title)} · набор ${Number.isInteger(r.case_index)?r.case_index+1:'—'}</h3><span class="badge result-${status}">${E(resultNames[r.status]||r.status)}</span></div><p class="caption">${E(r.message)} · ${E(r.duration_seconds??'—')} с</p><p class="info-note">${E(businessText(r.business_success))}</p>${(r.assertions||[]).length?assertionRows(r.assertions):'<p class="warning">Typed assertions отсутствуют. Успешность бизнес-требований не доказана.</p>'}${'actual' in r||'expected' in r?`<div class="expected-actual"><div><small>Ожидалось</small><strong>${E(valueText(r.expected))}</strong></div><div><small>Получено</small><strong>${E(valueText(r.actual))}</strong></div></div>`:''}<p class="helper">${E(r.engine||'—')} · Тестовые записи ${r.cleanup_succeeded?'удалены':r.cleanup_succeeded===false?'не удалось очистить':'не создавались'}</p><p class="helper">Источник evidence: ${E(r.evidence_provenance||'не указан')}${r.screenshot_unavailable_reason?' · '+E(r.screenshot_unavailable_reason):''} · пост-клик ${E(r.post_click_screenshot_bytes??'—')} Б · до отправки ${E(r.pre_submit_screenshot_bytes??'—')} Б</p>${r.error_provenance?details('Источник ошибки',r.error_provenance):''}${r.evidence_id?`<details><summary>Скриншот выполнения</summary><a href="/gateway/intelligence/api/v1/ai/evidence/${encodeURIComponent(r.evidence_id)}" target="_blank" rel="noopener"><img class="evidence-image" src="/gateway/intelligence/api/v1/ai/evidence/${encodeURIComponent(r.evidence_id)}" alt="Сохранённый снимок выполнения; источник указан выше" loading="lazy"></a></details>`:''}</article>`;
}
const codexStateNames={unavailable:'CLI недоступен',signed_out:'Вход ChatGPT не выполнен',pending:'Ожидается вход ChatGPT',authenticated:'ChatGPT подключён',error:'Ошибка подключения'};
function codexStatusLabel(status){return codexStateNames[status?.state]||'Статус подключения недоступен'}
function codexConnected(status){return Boolean(status?.available&&status.authenticated&&status.state==='authenticated')}
function allowMutations(){return $('#allowMutations')?.checked?'true':'false'}
function strictReadOnly(){return $('#strictReadOnly')?.checked?'true':'false'}
function suiteSource(s){
 if(s.provider==='codex')return 'Codex · ChatGPT'+(s.model?' · '+s.model:'');
 if(s.provider==='llm')return 'Нейросеть через API'+(s.model?' · '+s.model:'');
 if(s.provider==='local')return 'Встроенный генератор 1С (Playwright + OData)';
 if(s.provider==='demo')return 'Учебные правила · без нейросети';
 return 'Неизвестный источник';
}
function mountCodexConnection(initial,panel){
 let status=initial||{available:false,authenticated:false,state:'unavailable'},timer=null,revision=0,feedback='';
 function render(){
  clearTimeout(timer);
  if(!panel.isConnected)return;
  const connected=codexConnected(status),pending=status.state==='pending',expert=role==='expert';
  panel.innerHTML='<h2>Codex · вход через ChatGPT</h2><p class="caption">Официальный Codex CLI использует вход ChatGPT без API-ключа. Настройки API ниже независимы от этого подключения.</p>'+
   '<p class="warning">Одно подключение ChatGPT общее для всего локального стенда. При генерации контекст проекта передаётся OpenAI и расходует лимиты подписки ChatGPT.</p>'+
   '<p role="status" aria-live="polite"><span class="badge '+(connected?'ready':'pending')+'">'+E(codexStatusLabel(status))+'</span></p>'+
   (connected?'<p class="helper">Модель: '+E(status.model||'по умолчанию для аккаунта ChatGPT')+'</p>':'')+
   (!status.available?'<p class="helper">Codex CLI недоступен на сервере стенда. Обратитесь к администратору.</p>':'')+
   (status.state==='error'?'<p class="helper">Подключение не завершено. Повторите вход ChatGPT или отключите подключение.</p>':'')+
   '<div class="codex-device"></div><p class="helper">'+(expert?'Изменить подключение может только эксперт.':'Для входа или отключения обратитесь к пользователю с ролью expert.')+'</p>'+
   '<div class="actions"><button type="button" class="primary codex-login">Войти через ChatGPT</button><button type="button" class="secondary codex-logout">'+(pending?'Отменить вход':'Отключить ChatGPT')+'</button></div>'+
   (feedback?'<p class="warning" role="alert">'+E(feedback)+'</p>':'');
  if(pending&&expert){
   const device=panel.querySelector('.codex-device');
   let uri=null;
   try{
    const url=new URL(status.verification_uri);
    if(url.protocol==='https:'&&url.hostname==='auth.openai.com'&&url.pathname==='/codex/device'&&!url.port&&!url.username&&!url.password&&!url.search&&!url.hash)uri=url.href;
   }catch{}
   if(uri&&status.user_code){
    const instructions=document.createElement('p');instructions.className='info-note';
    const link=document.createElement('a');link.href=uri;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Открыть официальный вход OpenAI';
    const code=document.createElement('code');code.textContent=status.user_code;
    instructions.append(link,document.createTextNode(' и ввести код: '),code);
    device.append(instructions);
   }else{
    const waiting=document.createElement('p');waiting.className='info-note';waiting.textContent='Ожидаем официальный адрес и код от Codex CLI…';device.append(waiting);
   }
   const pollNote=document.createElement('p');pollNote.className='helper';pollNote.textContent='Не закрывайте эту страницу: статус входа обновляется автоматически. Вводите код только на официальном сайте OpenAI; не передавайте его другим людям.';device.append(pollNote);
  }
  const login=panel.querySelector('.codex-login'),logout=panel.querySelector('.codex-logout');
  login.disabled=!expert||!status.available||connected||pending;
  logout.disabled=!expert||!status.available||(!connected&&!pending&&status.state!=='error');
  login.onclick=()=>task(()=>change('login'));logout.onclick=()=>task(()=>change('logout'));
  busyButtons();
  if(pending)timer=setTimeout(poll,3000);
 }
 async function poll(){
  if(!panel.isConnected)return;
  const requestRevision=revision;
  try{
   const next=await api('intelligence','ai/codex/status');
   if(!panel.isConnected||requestRevision!==revision)return;
   status=next;feedback='';
  }catch{
   if(!panel.isConnected||requestRevision!==revision)return;
   feedback='Не удалось обновить статус Codex. Повторяем автоматически, пока ожидается вход.';
  }
  render();
 }
 async function change(action){
  if(role!=='expert')throw Error('Для изменения подключения Codex требуется роль expert.');
  revision++;clearTimeout(timer);
  try{status=await api('intelligence','ai/codex/'+action,'POST');feedback=''}
  catch{feedback=action==='login'?'Не удалось начать вход ChatGPT. Проверьте доступность Codex CLI и повторите попытку.':'Не удалось отключить ChatGPT. Обновите страницу и проверьте состояние подключения.';throw Error(feedback)}
  finally{render()}
 }
 render();
}
async function prepareGeneration(report,pid){
 const status=await api('intelligence','ai/status');
 const detail=await api('impact','analyses/'+report.analysis_id);
 $('#aiPreparation')?.remove();$('#projectAnalysisReport').insertAdjacentHTML('beforeend',`<div class="analysis-box" id="aiPreparation"><h2>Подготовка проверок</h2><p class="caption">Сервис передаст область влияния и контекст разработки генератору. Предложения проверяются и сохраняются до запуска.</p><label>Источник сценариев<select id="generationProvider"><option value="local" selected>Встроенный генератор сценариев 1С (Playwright + OData)</option><option value="llm" ${status.configured?'':'disabled'}>Нейросеть через API ${status.configured?'· '+E(status.model):'· ключ не настроен'}</option><option value="codex" ${codexConnected(status.codex)?'':'disabled'}>Codex · ChatGPT · ${E(codexStatusLabel(status.codex))}</option>${demoSuite(report.package)?'<option value="demo">Учебные правила — без нейросети</option>':''}</select></label><label class="inline"><input id="generationConsent" type="checkbox">Разрешаю передачу контекста проекта внешней нейросети при выборе API или Codex ChatGPT</label><p class="helper">Передаются изменения, граф, названия объектов и доступные фрагменты модели. Логины и пароли 1С в запрос не включаются. Непроверенные бинарные файлы не выдаются за разобранный код.</p><p class="warning">Codex использует одно подключение ChatGPT на весь локальный стенд. Контекст передаётся OpenAI и расходует лимиты подписки ChatGPT.</p><p class="info-note">Codex: ${E(codexStatusLabel(status.codex))}. ${codexConnected(status.codex)?'Вход ChatGPT выполнен; можно выбрать Codex.':'Для генерации войдите через ChatGPT на странице «Нейросеть и проверки».'}</p>${!status.configured?'<p class="info-note">API-ключ не настроен. Настройки API доступны на странице «Нейросеть и проверки»; ключ не сохраняется в браузере.</p>':''}${demoSuite(report.package)?'<p class="info-note">Учебный стенд presentation-sales: вариант «Учебные правила — без нейросети» формирует сценарии детерминированными правилами, без API-ключа и без Codex.</p>':''}<div id="generationFeedback"></div><div class="actions" id="generationActions"></div></div>`);
 const generate=async()=>{
  const provider=$('#generationProvider').value,consent=$('#generationConsent').checked;
  if((provider==='llm'||provider==='codex')&&!consent)throw Error('Подтвердите передачу контекста внешнему провайдеру (API или Codex ChatGPT).');
  if(provider==='codex'&&!codexConnected(status.codex))throw Error('Выполните вход ChatGPT на странице «Нейросеть и проверки».');
  $('#generationFeedback').innerHTML='';
  await api('impact',`analyses/${report.analysis_id}/deliver`,'POST');
  try{
   const suite=await api('intelligence','ai/suites','POST',{package_id:detail.package.package_id,project_id:pid,provider,consent,object_names:detail.object_names,model_context:detail.model_context||[]});
   activeSuite=suite.suite_id;await show(8);
  }catch(err){
   const text=String(err.message||'').replace(/^"|"$/g,'');
   if(provider==='llm'&&text.includes('Ключ нейросети не задан')){
    $('#generationFeedback').innerHTML=`<p class="warning">${E(text)}</p><button id="useLocalGenerator" class="secondary">Сформировать встроенным генератором</button>`;
    $('#useLocalGenerator').onclick=()=>task(async()=>{$('#generationProvider').value='local';await generate()});
    return;
   }
   throw err;
  }
 };
 addButton('#generationActions','Сформировать сценарии',generate);
 addButton('#generationActions','Настроить ChatGPT / API',async()=>{await show(8);$('#codexConnection')?.scrollIntoView({behavior:'smooth',block:'start'})},true);
 $('#aiPreparation').scrollIntoView({behavior:'smooth',block:'start'});
}
function llmSaveFeedback(res){
 if(!res)return '';
 if(res.persisted)return '<p class="success">Настройки записаны в '+E(res.env_file||'.env')+'. Модель: '+E(res.model)+' ('+E(res.endpoint_host||'')+')</p>';
 return '<p class="warning">Изменения только в памяти процесса: файл '+E(res.env_file||'.env')+' не изменён. Для постоянного хранения задайте TP_ALLOW_ENV_WRITE=1. Модель: '+E(res.model)+' ('+E(res.endpoint_host||'')+')</p>';
}
async function aiPage(){
 stopSuitePolling();
 heading('Нейросеть и проверки');const [status,suites,llmSettings]=await Promise.all([api('intelligence','ai/status'),api('intelligence','ai/suites?limit=100'),api('intelligence','ai/settings/llm')]);
 $('#content').innerHTML=`<div class="page-heading"><div><div class="eyebrow">ОТ ОБЛАСТИ ВЛИЯНИЯ К РЕЗУЛЬТАТУ</div><h1>Нейросеть и проверки</h1><p class="page-description">Просмотрите сценарии и тестовые данные, подтвердите план и изучите фактические результаты.</p></div></div><div class="connection-banner"><span class="badge ${llmSettings.has_key?'ready':'pending'}">${llmSettings.has_key?'API-ключ задан ('+E(llmSettings.model)+')':'Нейросеть: API-ключ не настроен'}</span><span class="badge ${status.browser_available?'ready':'pending'}">${status.browser_available?'Chromium доступен':'Chromium не найден'}</span><span class="badge">1С:Интеграция</span></div><div class="card" id="codexConnection"></div><div class="card"><h2>Нейросеть через API (OpenAI, DeepSeek, GigaChat, vLLM)</h2><p class="caption">Сервис отправляет изменённые и затронутые объекты метаданных 1С в модель через стандартный API /chat/completions.</p><div class="row"><label>URL провайдера API<input id="llmBaseUrl" value="${E(llmSettings.base_url||'https://api.openai.com/v1')}" placeholder="https://api.openai.com/v1"></label><label>Модель<input id="llmModel" value="${E(llmSettings.model||'gpt-4o-mini')}" placeholder="gpt-4o-mini, deepseek-chat и др."></label></div><label>API-ключ<input id="llmApiKey" type="password" placeholder="${llmSettings.has_key?'•••••••• (ключ уже сохранён; введите новый для замены)':'Введите API-ключ (sk-...)'}"></label><p class="caption">Изменения применяются только в памяти процесса. Для постоянного хранения нужен <code>TP_ALLOW_ENV_WRITE=1</code> (файл из <code>TP_ENV_FILE</code>, по умолчанию <code>.env</code>).</p><div class="actions" style="margin-top:12px"><button id="saveLlmSettings" class="primary">Сохранить настройки ИИ</button></div><div id="llmFeedback" style="margin-top:8px"></div></div><div class="card"><div class="history-header"><h2>Сохранённые планы проверок</h2><small>${suites.length} записей</small></div>${suites.length?table(['Создан','Источник','Статус','Прогон','Подтверждено','Изменения',''],suites.map(s=>[E(dateText(s.created_at)),E(suiteSource(s)),E(stateNames[s.status]),`${s.counts.passed}/${s.counts.failed}/${s.counts.error}/${s.counts.skipped}`,s.approved_count,(s.mutating_count?`<span class="badge warning">изменяет</span>`:'—'),`<button class="secondary open-ai-suite" data-suite="${E(s.suite_id)}">Открыть</button>`])):'<p class="caption">Выберите «Подготовить проверки» в результате анализа проекта.</p>'}</div><div id="suiteView"></div>`;
 
 $('#saveLlmSettings').disabled=role!=='expert';
 $('#llmFeedback').innerHTML=llmSaveFeedback(llmSaveState);
 mountCodexConnection(status.codex,$('#codexConnection'));
 $('#saveLlmSettings').onclick=()=>task(async()=>{
  if(role!=='expert')throw Error('Для изменения подключения API требуется роль expert.');
  const base=$('#llmBaseUrl').value.trim(),model=$('#llmModel').value.trim(),key=$('#llmApiKey').value.trim();
  const res=await api('intelligence','ai/settings/llm','PUT',{base_url:base,model:model,api_key:key||null});
  llmSaveState=res;$('#llmFeedback').innerHTML=llmSaveFeedback(res);
  await show(8);
 });

 document.querySelectorAll('.open-ai-suite').forEach(b=>b.onclick=()=>task(async()=>{activeSuite=b.dataset.suite;await renderSuite(await api('intelligence','ai/suites/'+activeSuite))}));
 if(activeSuite)await renderSuite(await api('intelligence','ai/suites/'+activeSuite));
}
async function renderSuite(s,focus=true){
 stopSuitePolling();
 if(!$('#suiteView'))return;
 activeSuite=s.suite_id;history.replaceState(null,'','#ai/'+s.suite_id);
 const proposals=s.suggestion.proposals;
 const selected=s.approval?.selected_proposals||proposals.map((_,i)=>i);
 const covered=new Set(proposals.filter((_,i)=>selected.includes(i)).flatMap(p=>p.object_ids||[]));
 const counts=s.counts||{};
 // История на этой странице должна отражать тот же сохранённый state, что и карточка.
 for(const button of document.querySelectorAll('.open-ai-suite')){
  if(button.dataset.suite!==s.suite_id)continue;
  const cells=button.closest('tr')?.cells;
  if(cells){cells[2].textContent=stateNames[s.status]||s.status;cells[3].textContent=['passed','failed','error','skipped'].map(k=>counts[k]??0).join('/')}
 }
 $('#suiteView').innerHTML=`<div class="card"><div class="history-header"><h2>План проверок · ${E(s.target_version)}</h2><span class="badge ${s.status==='completed'?'blue':'pending'}">${E(stateNames[s.status]||s.status)}</span></div><p class="${s.provider==='demo'?'warning':'info-note'}">${E(suiteSource(s))}. ${s.synthetic?'Синтетический проект.':''}</p><p>${E(s.suggestion.summary)}</p>${(s.suggestion.selected_test_ids||[]).length?`<p class="info-note">Выбраны тесты из каталога: ${s.suggestion.selected_test_ids.map(E).join(', ')}. Их исполнение требует внешнего runner; запуск ниже выполняет только предложенные сценарии.</p>`:''}<div class="analysis-summary"><div><strong>${proposals.length}</strong>Предложено сценариев</div><div><strong>${covered.size} / ${(s.affected_objects||[]).length}</strong>Объектов указано в сценариях</div><div><strong>${proposals.reduce((n,p)=>n+(p.cases||[]).length,0)}</strong>Наборов тестовых данных</div></div><p class="helper">Это плановое покрытие, не доказательство исправности. Connectivity/metadata passed не подтверждают бизнес-требования.</p>${proposals.map((p,i)=>proposalCard(p,i,s,selected)).join('')}<details><summary>Ограничения и предупреждения</summary>${[...(s.warnings||[]),...(s.suggestion.warnings||[])].map(notice).join('')}</details><div id="suiteFeedback" aria-live="polite">${s.message?notice(s.message):''}</div><div id="suiteActions"></div><div id="suiteResults"></div></div>`;
 if(s.status==='draft'){
  $('#suiteActions').innerHTML='<label>Обоснование подтверждения<input id="suiteReason" placeholder="Например, проверены сценарии и ожидаемые суммы"></label><p class="helper">Подтверждение сохраняет выбранные сценарии в каталог и подтверждает их привязки. Запуск выполняет сценарии на настроенной базе 1С.</p><div id="suiteApprove"></div>';
  if(role==='expert')addButton('#suiteApprove','Подтвердить выбранные сценарии',async()=>{const ids=[...document.querySelectorAll('.proposal-choice:checked')].map(x=>Number(x.value));if(!ids.length)throw Error('Выберите хотя бы один сценарий.');const r=await api('intelligence',`ai/suites/${s.suite_id}/approve`,'POST',{reason:$('#suiteReason').value,selected_proposals:ids});await renderSuite(r)});
 }else if(s.status==='approved'){
  if(demoSuite(s))addButton('#suiteActions','Запустить на учебном стенде',()=>runDemoSuite(s));
  else addButton('#suiteActions','Запустить на базе 1С',()=>enqueueSuite(s,'run-1c'));
 }
 if(!pendingSuite(s)){
  $('#suiteActions').insertAdjacentHTML('beforeend','<label class="checkbox"><input type="checkbox" id="allowMutations"> Разрешить изменяющие шаги (база тестовая)</label><label class="checkbox"><input type="checkbox" id="strictReadOnly"> Строгий режим: только проверки без кликов</label><p class="helper">Эвристика по подписи кнопки неполна. Отмена не откатывает уже выполненные действия в 1С.</p>');
  if(s.status!=='draft'&&(s.approval?.selected_proposals||[]).length){
   // Demo-план исполняется только на учебном стенде; rerun ушёл бы в исполнитель реальной 1С.
   if(demoSuite(s))$('#suiteActions').insertAdjacentHTML('beforeend','<p class="helper">Повторный прогон учебного demo-плана недоступен: он выполняется только на учебном стенде.</p>');
   else addButton('#suiteActions','Прогнать снова',()=>enqueueSuite(s,'rerun'));
  }
 }else{
  $('#suiteFeedback').insertAdjacentHTML('beforeend',`<p class="info-note">${E(stateNames[s.status])}. Попытка ${E(s.attempt_version??'—')} · ${s.cancel_requested?'Запрошена отмена; ожидаем остановки исполнителя.':'Можно покинуть страницу: прогон продолжится, статус восстановится после открытия.'}</p>`);
  if(!s.cancel_requested)addButton('#suiteActions','Отменить прогон',async()=>{
   const state=await api('intelligence',`ai/suites/${encodeURIComponent(s.suite_id)}/cancel`,'POST');
   await renderSuite({...await api('intelligence','ai/suites/'+encodeURIComponent(s.suite_id)),...state},false);
  },true);
 }
 if(s.status!=='draft'&&s.status!=='approved'||(s.results||[]).length){
  $('#suiteResults').innerHTML=`<div class="section-heading"><h2>Сохранённые результаты</h2><span class="badge blue">${E(stateNames[s.status]||s.status)}</span></div><div class="result-counts">${['passed','failed','error','skipped'].map(k=>`<div class="result-count ${k}"><strong>${E(counts[k]??0)}</strong>${E(resultNames[k])}</div>`).join('')}</div><p class="info-note">${E(businessText(s.business_success))}. Пройденные probes доступности/метаданных не означают, что бизнес-требования выполнены.</p>${['cancelled','interrupted','error','failed'].includes(s.status)?'<p class="warning">Прогон не завершён успешно. Уже выполненные действия и сохранённые результаты не отменены.</p>':''}${(s.results||[]).map(resultCard).join('')||'<p class="caption">Сохранённых результатов кейсов пока нет.</p>'}`;
 }
 $('#suiteActions').insertAdjacentHTML('beforeend',`<a class="secondary" href="/gateway/intelligence/api/v1/ai/suites/${encodeURIComponent(s.suite_id)}/report?format=md" download>Скачать отчёт о прогоне (Markdown)</a><a class="secondary" href="/gateway/intelligence/api/v1/ai/suites/${encodeURIComponent(s.suite_id)}/report?format=csv" download>CSV по кейсам</a>`);
 if(!pendingSuite(s))addButton('#suiteActions','Проверить план',async()=>{const r=await api('intelligence',`ai/suites/${encodeURIComponent(s.suite_id)}/preflight`);renderPreflight(r)});
 if(focus)$('#suiteView').scrollIntoView({behavior:'smooth',block:'start'});
 startSuitePolling(s);
 if(focus&&typeof refreshProjectRuns==='function')await refreshProjectRuns();
}
function renderPreflight(r){
 let host=$('#preflightBlock');
 if(!host){const card=document.querySelector('#suiteView .card');host=document.createElement('div');host.id='preflightBlock';card.insertBefore(host,card.firstChild)}
 const engines=(r.engines||[]).map(e=>`<li>${E(e.engine)}${e.steps_count==null?'':' · шагов '+E(e.steps_count)}${e.mutating?' · изменяет данные':''}${e.blocked_reason?` · <span class="warning">${E(e.blocked_reason)}</span>`:''}</li>`).join('');
 const warnings=(r.warnings&&r.warnings.length)?`<ul>${r.warnings.map(w=>`<li class="warning">${E(w)}</li>`).join('')}</ul>`:'<p class="helper">Предупреждений нет. Возможность запуска определена серверным would_run.</p>';
 host.innerHTML=`<div class="card"><div class="history-header"><h2>Preflight плана</h2><span class="badge ${r.would_run?'ready':'pending'}">${r.would_run?'Можно запускать':'Запуск заблокирован'}</span></div><p class="caption">Read-only проверка: браузер не запускается, в базу ничего не пишется.</p><ul>${engines||'<li>Нет утверждённых предложений</li>'}</ul>${warnings}<p class="caption">web_url: ${r.settings.web_url_present?'настроен':'нет'} · odata_url: ${r.settings.odata_url_present?'настроен':'нет'} · пользователь: ${r.settings.username_present?'есть':'нет'} · пароль: ${r.settings.has_password?'есть':'нет'} · grace: ${r.runtime.grace_seconds} с · ожидание формы: ${r.runtime.login_wait_seconds} с · submit: ${E(r.runtime.login_submit)} · предохранитель: ${r.runtime.login_max_failures} / ${r.runtime.login_cooldown_minutes} мин</p></div>`;
}
initializeWorkspace().catch(e=>msg(e.message,true));
