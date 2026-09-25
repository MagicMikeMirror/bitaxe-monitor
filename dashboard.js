/* Operational diagnostics and generation selection. Bitcoin remains fully visible. */
const selectedGeneration = new URLSearchParams(location.search).get('generation');
const originalFetch = window.fetch.bind(window);
window.fetch = (url, options) => {
  const scope = selectedGeneration || generationState?.active;
  if (typeof url === 'string' && url.startsWith('/api/') && scope) {
    const target = new URL(url, location.origin);
    target.searchParams.set('generation', scope);
    url = target.pathname + target.search;
  }
  return originalFetch(url, options);
};
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let generationState = null, archived = false, lastDiagnostics = null;
const unit = (value, suffix, decimals = 1) => value == null ? '—' : fmt(value, decimals) + ' ' + suffix;
const scaled = (value, factor) => value == null ? null : value / factor;
async function json(url, options) {
  const response = await fetch(url, options), result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Anfrage fehlgeschlagen');
  return result;
}
function element(tag, text, parent, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  if (parent) parent.append(node);
  return node;
}
const generationBar = element('section', null, null, 'card');
generationBar.style.margin = '18px 0';
document.querySelector('.grid').before(generationBar);
element('div', 'Gerät & Historie', generationBar, 'label');
const generationSelect = element('select', null, generationBar);
generationSelect.setAttribute('aria-label', 'Gerätegeneration');
const generationNotice = element('p', 'Bestehende Historie wird geladen …', generationBar, 'sub');
const switchButton = element('button', 'Gerätewechsel vorbereiten', generationBar);
const backupButton = element('button', 'Historie sichern', generationBar);
const storageNotice = element('span', '', generationBar, 'sub');
const buttonStyle = document.createElement('style');
buttonStyle.textContent = '.card{min-width:0}.value,.sub,.healthdetails{overflow-wrap:anywhere} select,button{font:inherit}main>section.card button,main>section.card select{background:#182231;color:#dce5f1;border:1px solid #34445a;border-radius:8px;padding:8px 12px;margin:8px 8px 8px 0}button:disabled{opacity:.45;cursor:not-allowed}.diagnostic-facts{display:flex;flex-wrap:wrap;gap:16px;margin:12px 0}.diagnostic-facts>span{min-width:130px}.diagnostic-chart{height:190px;margin-top:12px}.diagnostic-chart canvas{width:100%;height:100%}.timeline-filter{margin:8px 0}';
document.head.append(buttonStyle);
generationSelect.onchange = () => {
  const url = new URL(location.href);
  if (generationSelect.value === generationState.active) url.searchParams.delete('generation');
  else url.searchParams.set('generation', generationSelect.value);
  location.href = url;
};
async function generations() {
  generationState = await json('/api/generations');
  const selected = selectedGeneration || generationState.active;
  archived = selected !== generationState.active;
  generationSelect.replaceChildren();
  for (const g of generationState.generations) {
    const option = element('option', g.label + (g.ended_at ? ' · Archiv' : ''), generationSelect);
    option.value = g.id;
    option.selected = g.id === selected;
  }
  const pending = generationState.pending;
  generationNotice.textContent = archived ? 'Archivansicht · Messwerte und Vorfälle dieser Generation. Geräteaktionen sind gesperrt.' :
    pending ? 'Gerät erkannt. Die Identität muss zugeordnet werden; bisherige Messreihen bleiben getrennt.' :
    generationState.identity_confirmed ? 'Geräteidentität bestätigt. Statistik dieser Generation.' :
    'Warte auf Gerätezuordnung. Die bestehende Historie bleibt erhalten; ein Austauschgerät wurde noch nicht zugeordnet.';
  switchButton.disabled = archived;
  backupButton.disabled = archived;
}
switchButton.onclick = async () => {
  try {
    const preview = await json('/api/generations/preview');
    const g = preview.generations.find(g => g.id === preview.active);
    if (!preview.pending?.identity_available && !preview.identity_confirmed) {
      alert('Derzeit ist kein Gerät mit bestätigbarer Identität erreichbar. Die alte Historie bleibt erhalten. Bitte nach Ankunft des Austauschgeräts erneut öffnen.');
      return;
    }
    const dialog = document.createElement('dialog');
    element('h2', 'Gerät zuordnen', dialog);
    element('p', `${g.label}: ${preview.counts.samples.toLocaleString('de-DE')} Messwerte und ${preview.counts.incidents} Vorfälle bleiben erhalten.`, dialog);
    element('p', 'Für das Austauschgerät eine neue Historie beginnen. Nur wenn das angeschlossene Gerät tatsächlich das bisherige Gerät ist, darf es zur bestehenden Historie zugeordnet werden.', dialog);
    const input = element('input', null, dialog); input.value = 'Gamma 2 – Austausch'; input.maxLength = 80; input.setAttribute('aria-label', 'Name der Historie');
    const status = element('p', '', dialog);
    const create = element('button', 'Neue Historie beginnen – alte archivieren', dialog);
    const bind = !preview.identity_confirmed ? element('button', 'Dieses Gerät ist das Altgerät', dialog) : null;
    const cancel = element('button', 'Abbrechen', dialog);
    cancel.onclick = () => dialog.close();
    dialog.onclose = () => dialog.remove();
    document.body.append(dialog); dialog.showModal();
    const operation = (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(36).slice(2));
    async function apply(existing) {
      if (existing && !confirm('Ist das erreichbare Gerät nachweislich das bisherige Altgerät? Seine Messwerte würden der alten Historie zugeordnet.')) return;
      create.disabled = true; if (bind) bind.disabled = true;
      status.textContent = 'Gerät wird geprüft; Sicherung und Zuordnung laufen …';
      try {
        await json('/api/generations/switch', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
          expected:preview.active,token:preview.token,operation,label:existing ? g.label : input.value,confirmed:true,
          bind_existing:existing,existing_device_confirmed:existing})});
        location.href = location.pathname;
      } catch(error) { status.textContent = error.message; create.disabled = false; if(bind) bind.disabled = false; }
    }
    create.onclick = () => apply(false); if (bind) bind.onclick = () => apply(true);
  } catch(error) { alert(error.message); }
};
backupButton.onclick = async () => {
  backupButton.disabled = true;
  try { const result = await json('/api/backup', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); storageNotice.textContent = 'Geprüfte Sicherung gespeichert: ' + result.file; }
  catch(error) { storageNotice.textContent = error.message; }
  finally { backupButton.disabled = archived; }
};

const diagHost = $('events').parentElement;
diagHost.querySelector('.label').textContent = 'Domains & Diagnose';
$('events').hidden = true;
const facts = element('div', null, diagHost, 'diagnostic-facts'); facts.id = 'diagnosticFacts';
for (const [id, label] of [['domainChart','Vier Hash-Domains · GH/s'], ['errorChart','ASIC-Fehlerquote · %'], ['inputVoltageChart','Eingangsspannung · V'], ['heapChart','Interner Speicher / größter freier Block · KiB']]) {
  element('div', label, diagHost, 'sub');
  const wrapper = element('div', null, diagHost, 'diagnostic-chart');
  const canvas = element('canvas', null, wrapper); canvas.id = id;
}
const timelineHost = $('incidents').parentElement;
timelineHost.querySelector('.label').textContent = 'Zeitachse · Vorfälle & Bedienung';
const timelineFilter = element('select', null, timelineHost, 'timeline-filter');
for (const [value,label] of [['important','Vorfälle & Bedienung'],['incidents','Nur Vorfälle'],['all','Alle Ereignisse, einschließlich Shares']]) { const option=element('option',label,timelineFilter);option.value=value; }
timelineHost.insertBefore(timelineFilter, $('incidents'));
timelineFilter.onchange = () => incidents();
widgetMeta[12][1] = 'Zeitachse'; widgetMeta[13][1] = 'Domains & Diagnose';

current = async () => {
  const x = await json('/api/current'), d = x.data || {}, h = x.hashrate_history || {}, v = x.voltage_24h || {};
  const state = archived ? 'ARCHIVED' : (x.state || 'WAITING FOR DEVICE');
  $('dot').className = 'dot ' + (state === 'ONLINE' ? 'ok' : '');
  $('state').textContent = state;
  $('health').textContent = state === 'WAITING FOR DEVICE' ? 'Warte auf Gerät' : state === 'IDENTITY REQUIRED' ? 'Gerätezuordnung erforderlich' : state;
  $('healthText').textContent = x.summary || 'Die bestehende Historie bleibt erhalten.';
  $('ver').textContent = (d.version || 'AxeOS') + ' · Board ' + (d.boardVersion || '—') + ' · Messung ' + (x.age_seconds == null ? 'ausstehend' : dur(x.age_seconds) + ' alt');
  $('hash').textContent = unit(scaled(d.hashRate,1000), 'TH/s',2);
  $('hash10m').textContent = fmt(scaled(d.hashRate_10m,1000),2);
  for (const period of ['1h','24h','7d']) { $('hash'+period).textContent=fmt(scaled(h['avg_'+period],1000),2); $('cover'+period).textContent=coverage(h['coverage_pct_'+period],h['coverage_'+period]); }
  $('power').textContent=unit(d.power,'W');
  $('efficiency').textContent=unit(d.efficiencyJTh,'J/TH')+' · momentan, telemetriebasiert';
  $('voltage').textContent=unit(scaled(d.voltage,1000),'V',2)+' · '+unit(d.calculatedCurrent,'A',2)+' Eingang geschätzt';
  $('voltageStats').textContent=v.min==null?'24h —':'24h Min '+fmt(v.min/1000,2)+' · Ø '+fmt(v.avg/1000,2)+' · Max '+fmt(v.max/1000,2)+' V';
  $('temp').textContent=unit(d.temp,'°C');
  $('vr').textContent='VR '+unit(d.vrTemp,'°C')+' · Lüfter '+unit(d.fanrpm,'RPM',0)+' / '+unit(d.fanspeed,'%',0)+' '+(d.autofanspeed?'AUTO':'manuell');
  $('shares').textContent=(d.sharesAccepted??'—')+' / '+(d.sharesRejected??'—');
  $('best').textContent='Geräte-Best '+diff(d.bestDiff)+' · Boot-Best '+diff(d.bestSessionDiff);
  $('pool').textContent=d.isUsingFallbackStratum==null?'—':d.isUsingFallbackStratum?'FALLBACK':'PRIMÄR';
  $('errors').textContent='ASIC '+unit(d.errorPercentage,'%',2)+' · Pool-Reject '+unit(d.rejectRate,'%',2);
  $('uptime').textContent=dur(d.uptimeSeconds);$('uptime').parentElement.querySelector('.label').textContent='Laufzeit seit Neustart';
  $('wifi').textContent='Pool-Antwort '+unit(d.responseTime,'ms',0)+' · API '+unit(d.apiResponseMs,'ms',0);
  $('healthDetails').replaceChildren();
  const expected=d.expectedHashrate, ratio=expected>0 && d.hashRate!=null ? 100*d.hashRate/expected : null;
  for (const [label,value] of [['Soll/Ist Hashrate',unit(ratio,'%',1)],['Frequenz Soll/Ist',unit(d.frequency,'MHz',0)+' / '+unit(d.actualFrequency,'MHz',0)],['Core Soll/Ist',unit(d.coreVoltage,'mV',0)+' / '+unit(d.coreVoltageActual,'mV',0)],['Mining',d.miningPaused==null?'unbekannt':d.miningPaused?'pausiert':'aktiv'],['Reglerfehler',d.power_fault||'kein gemeldeter Fehler'],['Hardware',d.hardware_fault||'kein gemeldeter Fehler']]) {
    const span=element('span',label+': ',$('healthDetails'));element('b',value,span);
  }
  const details=element('details',null,$('healthDetails'));element('summary','Weitere Gerätedaten',details);
  element('p','WLAN '+(d.wifiStatus||'—')+' · '+unit(d.wifiRSSI,'dBm',0)+' · Resetgrund '+(d.resetReason||'—')+' · Pool-Ziel '+diff(d.poolDifficulty)+' · Netzwerk-Ziel '+diff(d.networkDifficulty)+' · Pending '+(d.sharesPending??'—')+' · Share-Batch '+(d.responseShareBatch??'—'),details);
};
const previousAutoStatus=autoRestartStatus;
autoRestartStatus=async()=>{await previousAutoStatus();$('autoRestartToggle').disabled=archived||!generationState?.identity_confirmed||!!generationState?.pending;};
const previousProfileStatus=profileStatus;
profileStatus=async()=>{await previousProfileStatus();document.querySelectorAll('#profileButtons button').forEach(b=>b.disabled=archived||!generationState?.identity_confirmed||!!generationState?.pending);};
const previousMarket=market;
market=async()=>{await previousMarket();$('minerName').textContent+=' · externe Pool-/Worker-Historie, unabhängig von Gerätegeneration';};
const previousIncidentDetail=incidentDetail;
incidentDetail=async id=>{
  await previousIncidentDetail(id);
  const incident=await json('/api/incidents/'+id), samples=incident.samples||[], w=incident.window;
  const host=$('incidentDetail').querySelector('.incidentcharts');
  const points=key=>samples.map((sample,index)=>({ts:sample.ts,v:sample[key],gap:index>0&&(sample.ts-samples[index-1].ts>30||sample.bootSession!==samples[index-1].bootSession)}));
  const options={start:w.start,end:w.end,range:'1h',decimals:0,markers:[{ts:incident.started_at,kind:'Erkennung',description:'Vorfall erkannt'}]};
  for(const [id,label,series] of [
    ['incidentCore','Core-Spannung Soll / Ist · mV',[['coreVoltage','Soll','#57a6ff'],['coreVoltageActual','Ist','#40e0a0']]],
    ['incidentFrequency','Frequenz Soll / Ist · MHz',[['frequency','Soll','#57a6ff'],['actualFrequency','Ist','#40e0a0']]],
    ['incidentFan','Lüfterdrehzahl · RPM',[['fanrpm','Lüfter','#57a6ff']]],
    ['incidentErrorPct','ASIC-Fehlerquote · %',[['errorPercentage','ASIC-Fehler','#b986ff']]],
  ]){
    const box=element('div',null,host,'incidentchart');element('div',label,box,'label');const canvas=element('canvas',null,box);canvas.id=id;
    draw(id,series.map(([key,name,color])=>({name,color,points:points(key)})),options);
  }
  const stats=incident.pre_stats||{}, maxTemp=stats.temp?.max, maxVr=stats.vrTemp?.max;
  element('p','Vorheriges 5-Minuten-Fenster: ASIC max. '+unit(maxTemp,'°C')+' · VR max. '+unit(maxVr,'°C')+'. Lücken und Bootgrenzen werden nicht verbunden.',$('incidentDetail'),'sub');
};

events = async () => {
  const x = await json('/api/diagnostics'); lastDiagnostics=x;
  const w=x.windows['5m'], values=x.samples||[];
  facts.replaceChildren();
  for (const [label,value] of [['Domains',`${w.active_domains}/${w.domain_count} aktiv · ${w.known_domains} bekannt`],['Asymmetrie',unit(w.domain_imbalance_pct,'%',1)],['ErrorCount Δ / 5m',w.error_delta==null?'—':fmt(w.error_delta,0)+(w.counter_interrupted?' · Teilintervalle':'')],['Reject / 5m',unit(w.reject_pct,'%',2)],['Effizienz / 5m',unit(w.efficiency_jth,'J/TH')],['Unterspannung / 5m',dur(w.voltage_low_seconds)],['Mining / Pause',dur(w.mining_seconds)+' / '+dur(w.pause_seconds)],['Datenabdeckung',dur(w.coverage_seconds)+' / 5m'],...['1m','5m','15m'].map(p=>['ASIC-Fehler / '+p,unit(x.windows[p].error_pct,'%',2)])]) {
    const span=element('span',label+': ',facts);element('b',value,span);
  }
  const pts=fn=>values.map((v,index)=>({ts:v.ts,v:fn(v),gap:index>0 && (v.ts-values[index-1].ts>x.poll_seconds*3||v.counterEpoch!==values[index-1].counterEpoch)}));
  const options={start:x.start,end:x.end,range:'1h',decimals:1};
  draw('domainChart',['#57a6ff','#ffc857','#ff5964','#b986ff'].map((color,index)=>({name:'Domain '+(index+1),unit:'GH/s',color,points:pts(v=>v.hashrateMonitor?.asics?.[0]?.domains?.[index])})),options);
  draw('errorChart',[{name:'ASIC-Fehler',unit:'%',color:'#b986ff',points:pts(v=>v.errorPercentage)}],{...options,min:0,decimals:2});
  draw('inputVoltageChart',[{name:'Input',unit:'V',color:'#40e0a0',points:pts(v=>scaled(v.voltage,1000))}],{...options,decimals:2});
  draw('heapChart',[{name:'Intern frei',unit:'KiB',color:'#57a6ff',points:pts(v=>scaled(v.freeHeapInternal,1024))},{name:'Größter Block',unit:'KiB',color:'#ffc857',points:pts(v=>scaled(v.maxAllocHeap,1024))}],options);
};
incidents = async () => {
  const rows=await json('/api/timeline'), host=$('incidents');host.replaceChildren();
  for (const row of rows.filter(row=>timelineFilter.value==='all'||(timelineFilter.value==='incidents'?row.source==='incident':row.kind!=='REJECTED_SHARE'))) {
    const item=element('div',null,host,'event'+(row.source==='incident'?' clickable':''));
    element('span',new Date(row.ts*1000).toLocaleString('de-DE'),item);
    element('b',row.kind,item,'sev-'+row.severity);element('span',row.message,item);
    if(row.source==='incident')item.onclick=()=>incidentDetail(row.id);
  }
  if(!host.children.length)element('p','Keine Ereignisse in dieser Ansicht',host,'sub');
};
history = async (range='1h') => {
  const x=await json('/api/history?range='+range),s=x.samples||[],pts=k=>s.map(v=>({ts:v.ts,v:v[k],gap:!!v.gap})),o={markers:x.markers||[],start:x.start,end:x.end,range};
  draw('hashrate',[{name:'Hashrate',unit:'GH/s',points:pts('hashrate'),color:'#40e0a0'}],{...o,decimals:0});
  draw('temperature',[{name:'ASIC',unit:'°C',points:pts('temp'),color:'#ff5964'},{name:'VR',unit:'°C',points:pts('vr_temp'),color:'#ffc857',dash:[7,5]}],{...o,unit:'°C',decimals:0});
  draw('powerchart',[{name:'Leistung',unit:'W',points:pts('power'),color:'#57a6ff'}],{...o,min:0,decimals:1});
};
async function refreshAll() {
  try { await generations();await refreshDashboard();await Promise.all([events(),incidents(),history(document.querySelector('.tabs[data-chart] button.active')?.dataset.r||'1h')]); }
  catch(error){generationNotice.textContent='Aktualisierung fehlgeschlagen: '+error.message;}
}
setupLayouts();refreshAll();market();
json('/api/storage').then(s=>{storageNotice.textContent=`${s.samples.toLocaleString('de-DE')} Messwerte · ${(s.bytes/1048576).toFixed(1)} MiB · keine automatische Löschung`;}).catch(()=>{});
setInterval(refreshAll,REFRESH_MS);setInterval(refreshClock,1000);setInterval(market,60000);
