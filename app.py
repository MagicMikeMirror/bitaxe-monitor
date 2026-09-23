import json
import os
import sqlite3
import statistics
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse, urlunparse


API_URL = os.getenv("BITAXE_API_URL", "http://192.168.1.100/api/system/info")
DB_PATH = os.getenv("DB_PATH", "/data/bitaxe.sqlite3")
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
PORT = int(os.getenv("PORT", "8080"))
POWER_HIGH = float(os.getenv("POWER_HIGH_W", "35"))
TEMP_HIGH = float(os.getenv("TEMP_HIGH_C", "75"))
HASHRATE_LOW = float(os.getenv("HASHRATE_LOW_GH", "750"))
EXPECTED_HASHRATE = float(os.getenv("EXPECTED_HASHRATE_GH", "0"))
OFFLINE_AFTER_POLLS = max(2, int(os.getenv("OFFLINE_AFTER_POLLS", "3")))
RECOVERY_POLLS = max(1, int(os.getenv("RECOVERY_POLLS", "3")))
USER_RESUME_GRACE_SECONDS = max(30, POLL_SECONDS * RECOVERY_POLLS)
STALL_AFTER_POLLS = max(2, int(os.getenv("STALL_AFTER_POLLS", "3")))
IDLE_POWER_W = float(os.getenv("IDLE_POWER_W", "8"))
VOLTAGE_LOW_V = float(os.getenv("VOLTAGE_LOW_V", "4.75"))
PUBLIC_POOL_API_URL = os.getenv("PUBLIC_POOL_API_URL", "https://public-pool.io:40557/api").rstrip("/")
BTC_PRICE_URL = os.getenv("BTC_PRICE_URL", "https://api.coinbase.com/v2/prices/BTC-EUR/spot")
BTC_HISTORY_URL = os.getenv("BTC_HISTORY_URL", "https://api.exchange.coinbase.com/products/BTC-EUR/candles?granularity=3600")
MARKET_SECONDS = max(60, int(os.getenv("MARKET_SECONDS", "300")))
AUTO_RESTART_ENABLED = os.getenv("AUTO_RESTART_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
AUTO_RESTART_LOSS = min(0.95, max(0.10, float(os.getenv("AUTO_RESTART_THRESHOLD_PCT", "70")) / 100))
AUTO_RESTART_AFTER_SECONDS = max(60, int(os.getenv("AUTO_RESTART_AFTER_SECONDS", "600")))
AUTO_RESTART_MIN_UPTIME = max(60, int(os.getenv("AUTO_RESTART_MIN_UPTIME_SECONDS", "900")))
AUTO_RESTART_COOLDOWN = max(300, int(os.getenv("AUTO_RESTART_COOLDOWN_SECONDS", "1800")))
AUTO_RESTART_VERIFY_SECONDS = max(300, int(os.getenv("AUTO_RESTART_VERIFY_SECONDS", "900")))
AUTO_RESTART_MAX_ATTEMPTS = 2
DOMAIN_STALL_POLLS = max(3, int(os.getenv("DOMAIN_STALL_POLLS", "3")))
DOMAIN_STALL_AFTER_SECONDS = max(30, int(os.getenv("DOMAIN_STALL_AFTER_SECONDS", "60")))

market_cache = {"updated": None, "btc_eur": None, "block_btc": None, "price_history": [],
                "price_change_pct": None, "price_low": None, "price_high": None,
                "miner": {}, "network": {}}
market_lock = threading.Lock()
miner_address = None
hashrate_cache = {"updated": 0, "data": {}}
hashrate_lock = threading.Lock()

ALLOWED = (
    "power", "voltage", "current", "temp", "vrTemp", "coreVoltageActual",
    "actualFrequency", "hashRate", "hashRate_1m", "hashRate_10m", "hashRate_1h",
    "errorPercentage", "sharesAccepted", "sharesRejected", "bestDiff",
    "bestSessionDiff", "poolDifficulty", "networkDifficulty", "responseTime",
    "uptimeSeconds", "totalUptimeSeconds", "resetReason", "blockFound",
    "overheat_mode", "wifiStatus", "wifiRSSI", "miningPaused",
    "isUsingFallbackStratum", "version", "boardVersion", "fanrpm",
    "power_fault", "hardware_fault", "sharesRejectedReasons", "smallCoreCount",
    "expectedHashrate", "fanSpeed", "fan2rpm", "blockHeight",
    "coinbaseValueTotalSatoshis", "coinbaseValueUserSatoshis",
    "ASICModel", "axeOSVersion", "idfVersion", "frequency", "coreVoltage",
    "overclockEnabled", "temptarget", "fanspeed", "autofanspeed",
    "manualFanSpeed", "stratumProtocol", "fallbackStratumProtocol",
    "primaryPoolIndex", "secondaryPoolIndex", "workReceived", "sharesPending",
    "blockSignals", "cpuUsage", "hostname", "hashrateMonitor",
    "totalHashes", "totalLog2Work", "processTime"
)

INCIDENT_KINDS = {
    "POWER_INTERRUPTION", "MINING_STALL", "SOFTWARE_RESTART",
    "NETWORK_OR_API_OUTAGE", "THERMAL_EVENT", "POOL_OR_STRATUM_ISSUE",
    "HASHRATE_DEGRADATION", "ASIC_DOMAIN_STALL", "UNKNOWN"
}

LAYOUT_WIDGETS = (
    "hashrate", "power", "temperatures", "shares", "pool", "uptime", "health",
    "mining-profile", "bitcoin", "public-pool", "hashrate-chart", "thermal-chart", "incidents", "events"
)

MINING_PROFILES = {
    "eco": {"label": "Eco", "frequency": 490, "coreVoltage": 1100, "temptarget": 65, "autofanspeed": 0, "fanspeed": 100, "overclockEnabled": 1, "custom": False},
    "standard": {"label": "Standard", "frequency": 525, "coreVoltage": 1150, "temptarget": 65, "autofanspeed": 0, "fanspeed": 100, "overclockEnabled": 1, "custom": False},
    "oc": {"label": "OC", "frequency": 650, "coreVoltage": 1180, "temptarget": 60, "autofanspeed": 0, "fanspeed": 100, "overclockEnabled": 1, "custom": True},
    "performance": {"label": "Performance", "frequency": 725, "coreVoltage": 1220, "temptarget": 57, "autofanspeed": 0, "fanspeed": 100, "overclockEnabled": 1, "custom": True},
}

HTML = r'''<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bitaxe Monitor</title>
<style>
:root{color-scheme:dark;--bg:#070a0f;--card:#101620;--muted:#8390a3;--text:#f3f6fb;--green:#40e0a0;--yellow:#ffc857;--red:#ff5964;--blue:#57a6ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 70% -20%,#172638,#070a0f 45%);color:var(--text);font:15px system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:1600px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;gap:20px;align-items:center}.brand{font-size:clamp(23px,3vw,40px);font-weight:800;letter-spacing:.03em}.status{display:flex;gap:9px;align-items:center;color:var(--muted)}.dot{width:11px;height:11px;border-radius:50%;background:var(--red);box-shadow:0 0 18px currentColor}.dot.ok{background:var(--green)}.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:20px 0}.card{background:linear-gradient(145deg,#121a25,#0d121a);border:1px solid #202b3a;border-radius:16px;padding:16px;min-width:0;box-shadow:0 10px 35px #0005}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.1em}.value{font-size:clamp(22px,2.4vw,38px);font-weight:750;margin-top:7px;white-space:nowrap}.sub{color:var(--muted);margin-top:4px;overflow:hidden;text-overflow:ellipsis}.hashstats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px;margin-top:9px}.hashstat{min-width:0;color:var(--muted);font-size:10px;text-transform:uppercase}.hashstat b{display:block;color:#dce5f1;font-size:13px;line-height:1.2;white-space:nowrap}.hashstat small{display:block;color:var(--muted);font-size:9px;white-space:nowrap}.wide{grid-column:span 3}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}.facts.poolfacts{grid-template-columns:repeat(4,1fr)}.fact{background:#0b1119;border-radius:10px;padding:10px}.fact b{display:block;font-size:18px;margin-top:3px}.healthdetails{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:12px;padding-top:11px;border-top:1px solid #202b3a;color:#bac5d4;font-size:13px}.healthdetails b{color:var(--text);font-weight:650}.pricechart{height:105px;margin:9px 0 2px}.pricechart canvas{width:100%;height:105px}.pricechange{font-weight:700}.pricechange.up{color:var(--green)}.pricechange.down{color:var(--red)}.chart{height:230px;position:relative}.chart canvas{width:100%;height:190px}.dual{height:230px;display:grid;grid-template-rows:1fr 1fr;gap:8px;margin-top:4px}.mini{min-height:0;position:relative}.mini canvas{width:100%;height:94px}.legend{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;color:var(--muted);font-size:12px}.key{display:inline-block;width:18px;height:3px;border-radius:2px;margin:0 6px 3px 0;vertical-align:middle}.key.asic{background:var(--red)}.key.vr{height:0;border-top:3px dashed var(--yellow)}.key.power{background:var(--blue)}.key.voltage{background:var(--green)}.tabs{display:flex;gap:7px}.tabs button{background:#182231;color:#bac5d4;border:0;border-radius:8px;padding:6px 12px;cursor:pointer}.tabs button.active{background:var(--blue);color:#04101d}.events{max-height:310px;overflow:auto}.event{display:grid;grid-template-columns:145px minmax(155px,190px) minmax(0,1fr);gap:12px;padding:10px 0;border-bottom:1px solid #202b3a;align-items:start}.event>*{min-width:0}.event>b{white-space:nowrap}.event>span:last-child{overflow-wrap:anywhere;line-height:1.45}.sev-warning{color:var(--yellow)}.sev-critical{color:var(--red)}.sev-info{color:var(--green)}@media(max-width:1050px){.grid{grid-template-columns:repeat(3,1fr)}.wide{grid-column:span 3}.facts.poolfacts{grid-template-columns:repeat(2,1fr)}}@media(max-width:620px){.wrap{padding:13px}.grid{grid-template-columns:1fr 1fr}.wide{grid-column:span 2}.facts,.facts.poolfacts{grid-template-columns:1fr}.hashstats{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:8px}.event{grid-template-columns:1fr;gap:4px}.event>b{white-space:normal}.top{align-items:flex-start;flex-direction:column}.chart{height:210px}.dual{height:220px}}
.status{flex-wrap:wrap;justify-content:flex-end}.refresh{font-variant-numeric:tabular-nums;white-space:nowrap}
.healthhead{display:flex;align-items:center;justify-content:space-between;gap:16px}.autoswitch{display:inline-flex;align-items:center;gap:9px;color:var(--muted);font-size:13px;cursor:pointer;white-space:nowrap}.autoswitch input{position:absolute;opacity:0;pointer-events:none}.switchtrack{width:42px;height:24px;border-radius:14px;background:#273343;border:1px solid #39485c;position:relative;transition:.2s}.switchtrack:after{content:"";position:absolute;width:18px;height:18px;left:2px;top:2px;border-radius:50%;background:#9aa7b8;transition:.2s}.autoswitch input:checked+.switchtrack{background:#176c51;border-color:var(--green)}.autoswitch input:checked+.switchtrack:after{transform:translateX(18px);background:var(--green)}.autoswitch input:focus-visible+.switchtrack{outline:2px solid var(--blue);outline-offset:2px}.autoswitch input:disabled+.switchtrack{opacity:.55}.switchstate{min-width:38px;color:var(--text);font-weight:650}@media(max-width:620px){.healthhead{align-items:flex-start}.autoswitch{white-space:normal}}
.event.clickable{cursor:pointer}.event.clickable:hover{background:#152030}dialog{width:min(1180px,96vw);max-height:90vh;overflow:auto;background:#0d141e;color:var(--text);border:1px solid #34445a;border-radius:16px;padding:20px}dialog::backdrop{background:#000b}.detailgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.detailbox{background:#101b28;padding:10px;border-radius:9px;overflow-wrap:anywhere}.incidentcharts{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.incidentchart{height:190px;background:#0a111a;border:1px solid #202b3a;border-radius:10px;padding:9px}.incidentchart canvas{width:100%;height:155px}.closebtn{float:right;background:#223047;color:white;border:0;border-radius:8px;padding:8px 12px;cursor:pointer}@media(max-width:720px){.detailgrid,.incidentcharts{grid-template-columns:1fr}}
.layoutbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:16px}.layoutbar select,.layoutbar button{background:#182231;color:#dce5f1;border:1px solid #34445a;border-radius:8px;padding:7px 10px}.layoutbar button{cursor:pointer}.layoutdirty{color:var(--yellow);font-size:12px}.widgetbar{display:none;align-items:center;gap:7px;margin-bottom:10px;padding-bottom:8px;border-bottom:1px dashed #34445a}.widgetbar b{margin-right:auto}.widgetbar button{background:#1b293a;color:#dce5f1;border:0;border-radius:6px;padding:5px 8px;cursor:pointer}.grid.layout-edit .widgetbar,.grid:not(.layout-edit)>.card.is-collapsed .widgetbar{display:flex}.grid.layout-edit>.card{outline:1px dashed #52657d;cursor:grab}.grid.layout-edit>.card.is-hidden{display:block;opacity:.38}.grid:not(.layout-edit)>.card.is-hidden,.card[hidden]{display:none!important}.grid:not(.layout-edit)>.card.is-collapsed{align-self:start}.grid:not(.layout-edit)>.card.is-collapsed .widgetbar{margin:0;padding:0;border:0}.grid:not(.layout-edit)>.card.is-collapsed [data-action="hide"]{display:none}.card.is-collapsed .widget-body{display:none}.card.dragging{opacity:.35}.card.dragover{outline:2px solid var(--blue)!important}.widget-body{display:contents}.dot.pause{background:var(--blue)}
</style></head><body><main class="wrap"><div class="top"><div><div class="brand">₿ BITAXE GAMMA 601</div><div class="sub" id="ver">AxeOS</div></div><div class="status"><span class="dot" id="dot"></span><b id="state">WARTE AUF DATEN</b><span class="refresh" id="seen">Refresh —</span></div></div>
<section class="grid"><div class="card"><div class="label">Hashrate</div><div class="value" id="hash">—</div><div class="hashstats"><div class="hashstat">10m<b id="hash10m">—</b></div><div class="hashstat">1h<b id="hash1h">—</b><small id="cover1h"></small></div><div class="hashstat">24h<b id="hash24h">—</b><small id="cover24h"></small></div><div class="hashstat">7d<b id="hash7d">—</b><small id="cover7d"></small></div></div></div><div class="card"><div class="label">Leistung</div><div class="value" id="power">—</div><div class="sub" id="efficiency">— J/TH</div><div class="sub" id="voltage">—</div><div class="sub" id="voltageStats">24h —</div></div><div class="card"><div class="label">ASIC / VR</div><div class="value" id="temp">—</div><div class="sub" id="vr">—</div></div><div class="card"><div class="label">Shares</div><div class="value" id="shares">—</div><div class="sub" id="best">—</div></div><div class="card"><div class="label">Pool / Fehler</div><div class="value" id="pool">—</div><div class="sub" id="errors">—</div></div><div class="card"><div class="label">Laufzeit</div><div class="value" id="uptime">—</div><div class="sub" id="wifi">—</div></div>
<div class="card" style="grid-column:1/-1"><div class="healthhead"><div class="label">Health & Gerätestatus</div><label class="autoswitch" title="Automatischen AxeOS-Neustart bei anhaltendem Hashrate-Einbruch ein- oder ausschalten"><input id="autoRestartToggle" type="checkbox" disabled><span class="switchtrack"></span><span>Auto-Restart</span><span class="switchstate" id="autoRestartState">—</span></label></div><div class="value" id="health" style="font-size:20px">—</div><div class="sub" id="healthText">—</div><div class="healthdetails" id="healthDetails"></div></div>
<div class="card wide"><div class="label">Mining-Profil</div><div class="value" id="profileActive" style="font-size:24px">—</div><div class="sub" id="profileCurrent">Frequenz · Spannung · Kühlung</div><div class="tabs" id="profileButtons" style="margin-top:12px"><button data-profile="eco">Eco</button><button data-profile="standard">Standard</button><button data-profile="oc">OC</button><button data-profile="performance">Performance</button></div><div class="sub" id="profileHint">Alle Profile verwenden feste 100 % Lüfterleistung – ohne Neustart.</div></div>
<div class="card wide"><div class="label">Bitcoin & Blockwert</div><div class="value" id="btcEur">—</div><div class="sub"><span id="priceUpdated">Aktueller BTC/EUR-Kurs</span> · <span class="pricechange" id="priceChange">24h —</span></div><div class="pricechart"><canvas id="btcPriceChart"></canvas></div><div class="facts"><div class="fact" title="Neu erzeugte Bitcoin pro Block gemäß aktuellem Halving-Zyklus."><span class="sub">Block-Subvention</span><b id="blockSubsidy">— BTC</b></div><div class="fact" title="Gebühren der Transaktionen im aktuellen Blocktemplate. Sie kommen zusätzlich zur Block-Subvention hinzu."><span class="sub">Transaktionsgebühren</span><b id="blockFees">—</b></div><div class="fact" title="Der aktuell deiner Mining-Adresse zugewiesene Coinbase-Wert inklusive Transaktionsgebühren."><span class="sub" id="blockValueLabel">Aktueller Blockwert</span><b id="blockBtc">— BTC</b><span class="sub" id="blockEur">—</span></div></div><div class="sub">Blockhöhe <span id="blockHeight">—</span></div></div>
<div class="card wide"><div class="label">Mein Public-Pool-Miner</div><div class="value" id="minerHash">—</div><div class="sub" id="minerName">Worker —</div><div class="facts poolfacts"><div class="fact"><span class="sub">Best Difficulty</span><b id="minerBest">—</b></div><div class="fact"><span class="sub">Worker</span><b id="minerWorkers">—</b></div><div class="fact"><span class="sub">Solo Work</span><b id="minerWork">—</b></div><div class="fact"><span class="sub">Last Seen</span><b id="minerSeen">—</b></div></div></div>
<div class="card wide"><div class="top"><div><div class="label">Hashrate</div><div class="sub">GH/s</div></div><div class="tabs" data-chart="hashrate"><button data-r="1h" class="active">1h</button><button data-r="24h">24h</button><button data-r="7d">7d</button></div></div><div class="chart"><canvas id="hashrate"></canvas></div></div>
<div class="card wide"><div class="top"><div><div class="label">Leistung & Temperatur</div><div class="legend"><span><i class="key asic"></i>ASIC °C</span><span><i class="key vr"></i>VR °C</span><span><i class="key power"></i>Leistung W</span><span><i class="key voltage"></i>Input V</span></div></div><div class="tabs" data-chart="thermal"><button data-r="1h" class="active">1h</button><button data-r="24h">24h</button><button data-r="7d">7d</button></div></div><div class="dual"><div class="mini"><canvas id="temperature"></canvas></div><div class="mini"><canvas id="powerchart"></canvas></div></div></div>
<div class="card wide"><div class="label">Incidents</div><div class="events" id="incidents"></div></div><div class="card wide"><div class="label">Ereignisse</div><div class="events" id="events"></div></div></section></main><dialog id="incidentDialog"><button class="closebtn" onclick="$('incidentDialog').close()">Schließen</button><div id="incidentDetail"></div></dialog>
<script>
const $=id=>document.getElementById(id), fmt=(v,d=1)=>v==null?'—':Number(v).toFixed(d), dur=s=>{if(s==null)return'—';let d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60),x=Math.floor(s%60);if(d)return d+'d '+h+'h '+m+'m';if(h)return h+'h '+m+'m';return m+'m '+x+'s'}, diff=v=>{if(v==null)return'—';if(v>=1e12)return(v/1e12).toFixed(2)+'T';if(v>=1e9)return(v/1e9).toFixed(2)+'G';if(v>=1e6)return(v/1e6).toFixed(2)+'M';if(v>=1e3)return(v/1e3).toFixed(2)+'K';return String(v)};
new MutationObserver(()=>{$('dot').classList.toggle('pause',$('state').textContent==='USER PAUSED')}).observe($('state'),{childList:true});
const REFRESH_MS=10000;let lastRefreshAt=0,nextRefreshAt=0;function markRefresh(){lastRefreshAt=Date.now();nextRefreshAt=lastRefreshAt+REFRESH_MS;refreshClock()}function refreshClock(){if(!lastRefreshAt){$('seen').textContent='Refresh —';return}let next=Math.max(0,Math.ceil((nextRefreshAt-Date.now())/1000)),stamp=new Date(lastRefreshAt).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit'});$('seen').textContent='Refresh '+stamp+' · nächster in '+next+'s'}
const coverage=(pct,seconds)=>pct==null?'':fmt(pct,0)+'%';
async function current(){let r=await fetch('/api/current'),x=await r.json(),d=x.data||{},h=x.hashrate_history||{},v=x.voltage_24h||{},st=x.state||(x.online?'ONLINE':'OFFLINE');$('dot').className='dot '+(st==='ONLINE'?'ok':'');$('state').textContent=st;$('health').textContent='STATUS: '+st;$('healthText').textContent=x.summary||'—';$('seen').textContent=x.age_seconds==null?'':'vor '+Math.round(x.age_seconds)+'s';$('ver').textContent=(d.version||'AxeOS')+' · Board '+(d.boardVersion||'—');$('hash').textContent=fmt(d.hashRate/1000,2)+' TH/s';$('hash10m').textContent=fmt(d.hashRate_10m/1000,2);$('hash1h').textContent=fmt(h.avg_1h/1000,2);$('hash24h').textContent=fmt(h.avg_24h/1000,2);$('hash7d').textContent=fmt(h.avg_7d/1000,2);$('cover1h').textContent=coverage(h.coverage_pct_1h,h.coverage_1h);$('cover24h').textContent=coverage(h.coverage_pct_24h,h.coverage_24h);$('cover7d').textContent=coverage(h.coverage_pct_7d,h.coverage_7d);$('power').textContent=fmt(d.power)+' W';$('efficiency').textContent=d.efficiencyJTh==null?'— J/TH':fmt(d.efficiencyJTh,1)+' J/TH';$('voltage').textContent=fmt(d.voltage/1000,2)+' V · '+fmt(d.calculatedCurrent,2)+' A berechnet';$('voltageStats').textContent=v.min==null?'24h —':'24h Min '+fmt(v.min/1000,2)+' · Ø '+fmt(v.avg/1000,2)+' · Max '+fmt(v.max/1000,2)+' V';$('temp').textContent=fmt(d.temp)+' °C';$('vr').textContent='VR '+fmt(d.vrTemp)+' °C · '+fmt(d.fanrpm,0)+' RPM';$('shares').textContent=(d.sharesAccepted??'—')+' / '+(d.sharesRejected??'—');$('best').textContent='Best '+diff(d.bestDiff)+' · Reject '+fmt(d.rejectRate,2)+'%';$('pool').textContent=d.isUsingFallbackStratum?'FALLBACK':'PRIMÄR';$('errors').textContent='Fehler '+fmt(d.errorPercentage,2)+'% · '+fmt(d.responseTime,0)+' ms';$('uptime').textContent=dur(d.uptimeSeconds);$('wifi').textContent=(d.wifiStatus||'—')+' · '+(d.wifiRSSI??'—')+' dBm';$('healthDetails').innerHTML=[['Mining',d.miningPaused?'pausiert':'aktiv'],['Power Fault',d.power_fault||'nein'],['Reset',d.resetReason||'—'],['Frequenz',fmt(d.actualFrequency,0)+' MHz'],['Erwartete Hashrate',fmt(d.expectedHashrate/1000,3)+' TH/s'],['Core',fmt(d.coreVoltageActual,0)+' mV']].map(v=>'<span>'+v[0]+': <b>'+v[1]+'</b></span>').join('')}
const eur=v=>v==null?'—':new Intl.NumberFormat('de-DE',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(v), rate=v=>{if(v==null)return'—';if(v>=1e18)return(v/1e18).toFixed(2)+' EH/s';if(v>=1e15)return(v/1e15).toFixed(2)+' PH/s';if(v>=1e12)return(v/1e12).toFixed(2)+' TH/s';return diff(v)+' H/s'};
async function market(){let r=await fetch('/api/market'),x=await r.json(),m=x.miner||{},w=m.workers?.[0]||{},p=x.price_history||[],b=x.block_value||{},chg=x.price_change_pct,color=(chg??0)>=0?'#40e0a0':'#ff5964';$('blockSubsidy').textContent=b.subsidy_btc==null?'— BTC':Number(b.subsidy_btc).toFixed(4)+' BTC';$('blockFees').textContent=b.fees_btc==null?'nicht verfügbar':Number(b.fees_btc).toFixed(8)+' BTC';$('blockBtc').textContent=b.miner_btc==null?'— BTC':Number(b.miner_btc).toFixed(b.coinbase_available?8:4)+' BTC';$('blockValueLabel').textContent=b.coinbase_available?'Aktueller Blockwert':'Blockwert ohne aktuelle Transaktionsgebühren';$('btcEur').textContent=eur(x.btc_eur);$('priceUpdated').textContent='BTC/EUR Spot · Coinbase · '+(x.updated?new Date(x.updated*1000).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}):'nicht verfügbar');$('priceChange').textContent=chg==null?'24h —':'24h '+(chg>=0?'+':'')+Number(chg).toFixed(2)+'%';$('priceChange').className='pricechange '+((chg??0)>=0?'up':'down');$('blockEur').textContent=b.miner_eur==null?'—':'≈ '+eur(b.miner_eur);$('blockHeight').textContent=b.height?.toLocaleString('de-DE')||'—';if(p.length)draw('btcPriceChart',[{name:'BTC/EUR',unit:'€',points:p.map(v=>({ts:v.ts,v:v.close})),color:color,width:2.5}],{start:p[0].ts,end:p[p.length-1].ts,range:'24h',decimals:0,unit:'€'});$('minerHash').textContent=rate(m.hashRate);$('minerName').textContent='Worker '+(w.name||'—')+' · '+String(w.payoutMode||'solo').toUpperCase();$('minerBest').textContent=diff(m.bestDifficulty);$('minerWorkers').textContent=m.workersCount??'—';$('minerWork').textContent=diff(m.soloWork);$('minerSeen').textContent=m.lastSeen?new Date(m.lastSeen).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}):'—'}
const chartState={};function axisLabel(ts,range){let d=new Date(ts*1000);return range==='7d'?d.toLocaleDateString('de-DE',{weekday:'short',day:'2-digit',month:'2-digit'}):d.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'})}
function draw(id,series,opt={}){let c=$(id),ctx=c.getContext('2d'),w=c.clientWidth,h=c.clientHeight,d=devicePixelRatio,left=46,right=10,top=10,bottom=25,span=Math.max(1,opt.end-opt.start);c.width=w*d;c.height=h*d;ctx.scale(d,d);ctx.clearRect(0,0,w,h);let vals=series.flatMap(s=>s.points.filter(p=>p.v!=null&&!p.gap).map(p=>p.v));if(!vals.length){ctx.fillStyle='#8390a3';ctx.fillText('Noch keine Verlaufsdaten',left,26);return}let min=opt.min??Math.min(...vals),max=opt.max??Math.max(...vals);if(min===max){min-=1;max+=1}let px=t=>left+(t-opt.start)*(w-left-right)/span,py=v=>top+(max-v)*(h-top-bottom)/(max-min);ctx.font='11px system-ui';ctx.strokeStyle='#263447';ctx.fillStyle='#8390a3';for(let i=0;i<3;i++){let y=top+i*(h-top-bottom)/2,v=max-i*(max-min)/2;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();ctx.fillText(v.toFixed(opt.decimals??0)+(opt.unit||''),2,y+4)}let ticks=opt.range==='1h'?6:(opt.range==='24h'?6:7);for(let i=0;i<=ticks;i++){let t=opt.start+span*i/ticks,x=px(t),label=axisLabel(t,opt.range);ctx.fillText(label,Math.max(left,Math.min(w-right-54,x-22)),h-5)}(opt.markers||[]).filter(m=>m.planned&&m.ended_at).forEach(m=>{ctx.fillStyle='#57a6ff22';ctx.fillRect(px(Math.max(opt.start,m.ts)),top,Math.max(2,px(Math.min(opt.end,m.ended_at))-px(Math.max(opt.start,m.ts))),h-top-bottom)});(opt.markers||[]).forEach(m=>{let x=px(m.ts);ctx.strokeStyle=m.planned?'#57a6ff':(m.automatic?'#ffc857':'#ff5964');ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,h-bottom);ctx.stroke();ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(x-4,top);ctx.lineTo(x+4,top);ctx.lineTo(x,top+7);ctx.fill()});series.forEach(s=>{ctx.strokeStyle=s.color;ctx.lineWidth=s.width||2;ctx.setLineDash(s.dash||[]);ctx.beginPath();let started=false;s.points.forEach(p=>{if(p.v==null||p.gap){started=false;return}let x=px(p.ts),y=py(p.v);started?ctx.lineTo(x,y):(ctx.moveTo(x,y),started=true)});ctx.stroke()});ctx.setLineDash([]);chartState[id]={series,opt,left,right,top,bottom,w,h,px,py};c.onmousemove=e=>{let state=chartState[id],rect=c.getBoundingClientRect(),mx=e.clientX-rect.left,t=opt.start+(mx-left)*span/(w-left-right),points=series.flatMap(s=>s.points.filter(p=>p.v!=null&&!p.gap).map(p=>({...p,name:s.name||'',unit:s.unit||''}))),nearest=points.reduce((a,p)=>!a||Math.abs(p.ts-t)<Math.abs(a.ts-t)?p:a,null),marker=(opt.markers||[]).reduce((a,m)=>!a||Math.abs(m.ts-t)<Math.abs(a.ts-t)?m:a,null);c.title=marker&&Math.abs(px(marker.ts)-mx)<7?new Date(marker.ts*1000).toLocaleString('de-DE')+' · '+marker.kind+' · '+marker.description:nearest?new Date(nearest.ts*1000).toLocaleString('de-DE')+' · '+nearest.name+': '+Number(nearest.v).toFixed(2)+' '+nearest.unit:''}}
async function history(range='1h'){let r=await fetch('/api/history?range='+range),x=await r.json(),s=x.samples||[],pts=k=>s.map(v=>({ts:v.ts,v:v[k],gap:!!v.gap})),o={markers:x.markers||[],start:x.start,end:x.end,range};draw('hashrate',[{name:'Hashrate',unit:'GH/s',points:pts('hashrate'),color:'#40e0a0'}],{...o,decimals:0});draw('temperature',[{name:'ASIC',unit:'°C',points:pts('temp'),color:'#ff5964',width:2.5},{name:'VR',unit:'°C',points:pts('vr_temp'),color:'#ffc857',width:2.5,dash:[7,5]}],{...o,min:20,max:85,unit:'°',decimals:0});draw('powerchart',[{name:'Leistung',unit:'W',points:pts('power'),color:'#57a6ff'},{name:'Input',unit:'V',points:pts('voltage'),color:'#40e0a0'}],{...o,min:0,max:40,decimals:1})}
async function events(){let r=await fetch('/api/events'),x=await r.json();$('events').innerHTML=x.length?x.map(e=>'<div class="event"><span>'+new Date(e.ts*1000).toLocaleString()+'</span><b class="sev-'+e.severity+'">'+e.kind+'</b><span>'+e.message+'</span></div>').join(''):'<div class="sub" style="padding-top:12px">Noch keine Ereignisse</div>'}
async function incidentDetail(id){
 let r=await fetch('/api/incidents/'+id),i=await r.json(),f=i.facts||{},fx=i.forensics||{},d=(i.diagnostics||[]).at(-1),o=d?.observed||i.before_sample||{},asic=o.hashrateMonitor?.asics?.[0]||{},domains=asic.domains||[],err=asic.errorCount??fx.error_count,delta=fx.error_count_delta,s=i.samples||[],w=i.window||{start:i.started_at-300,end:(i.ended_at||Date.now()/1000)+300};
 let showV=v=>v==null?'—':fmt(v/1000,2)+' V',asicParts=[];if(o.actualFrequency!=null)asicParts.push(fmt(o.actualFrequency,0)+' MHz');if(o.coreVoltage!=null)asicParts.push(fmt(o.coreVoltage,0)+' mV konfiguriert');if(o.coreVoltageActual!=null)asicParts.push(showV(o.coreVoltageActual)+' gemessen');
 let boxes=[['Beginn',new Date(i.started_at*1000).toLocaleString('de-DE')],['Dauer',dur((i.ended_at||Date.now()/1000)-i.started_at)],['Vor Neustart',o.hashRate==null?null:fmt(o.hashRate/1000,2)+' TH/s'+(o.power==null?'':' · '+fmt(o.power,1)+' W')+(o.voltage==null?'':' · '+showV(o.voltage))],['Temperatur',o.temp==null?null:'ASIC '+fmt(o.temp,1)+' °C'+(o.vrTemp==null?'':' · VR '+fmt(o.vrTemp,1)+' °C')],['Domains (AxeOS Raw)',domains.length?domains.map(v=>fmt(v,2)).join(' / ')+' GH/s':null],['ASIC',asicParts.length?asicParts.join(' · '):null],['Error Count',err==null?null:Number(err).toLocaleString('de-DE')+(delta==null?'':' · Δ +'+Number(delta).toLocaleString('de-DE'))],['Input Voltage',fx.voltage_incident==null?null:'Incident '+showV(fx.voltage_incident)+' · 60s Min '+showV(fx.voltage_min_60s)+' · 5m Min '+showV(fx.voltage_min_5m)],['Diagnosequelle',d?.source_status||null],['Recovery',i.recovery||null]].filter(v=>v[1]!=null);
 $('incidentDetail').innerHTML='<h2>'+i.kind+'</h2><p>'+i.summary+'</p><div class="detailgrid">'+boxes.map(v=>'<div class="detailbox"><span class="sub">'+v[0]+'</span><br><b>'+v[1]+'</b></div>').join('')+'</div><div class="incidentcharts"><div class="incidentchart"><div class="label">Hashrate & Domains</div><canvas id="incidentHash"></canvas></div><div class="incidentchart"><div class="label">Input Voltage</div><canvas id="incidentVoltage"></canvas></div><div class="incidentchart"><div class="label">Power & Temperaturen</div><canvas id="incidentThermal"></canvas></div><div class="incidentchart"><div class="label">Error Count</div><canvas id="incidentErrors"></canvas></div></div><h3>Einordnung</h3><p>Messwerte sind beobachtet. Domain-Werte und Error Count werden unverändert aus AxeOS übernommen. Der Error Count enthält keine Information über die konkrete Fehlerart; ein Counter-Reset wird nicht als negatives Delta dargestellt.</p>';
 $('incidentDialog').showModal();
 let markers=[{ts:i.started_at,kind:i.kind,description:'Incident',automatic:false},...(i.events||[]).map(e=>({ts:e.ts,kind:e.kind,description:e.message,automatic:!!e.automatic}))],pts=fn=>s.map(v=>({ts:v.ts,v:fn(v)})),domain=n=>pts(v=>v.hashrateMonitor?.asics?.[0]?.domains?.[n]);
 draw('incidentHash',[{name:'Gesamt',unit:'GH/s',points:pts(v=>v.hashRate),color:'#40e0a0',width:2.5},...['#57a6ff','#ffc857','#ff5964','#b986ff'].map((c,n)=>({name:'Domain '+n,unit:'GH/s',points:domain(n),color:c}))],{start:w.start,end:w.end,range:'1h',markers,decimals:0});
 draw('incidentVoltage',[{name:'Input',unit:'V',points:pts(v=>v.voltage==null?null:v.voltage/1000),color:'#40e0a0',width:2.5}],{start:w.start,end:w.end,range:'1h',markers,decimals:2,unit:'V'});
 draw('incidentThermal',[{name:'Leistung',unit:'W',points:pts(v=>v.power),color:'#57a6ff'},{name:'ASIC',unit:'°C',points:pts(v=>v.temp),color:'#ff5964'},{name:'VR',unit:'°C',points:pts(v=>v.vrTemp),color:'#ffc857',dash:[7,5]}],{start:w.start,end:w.end,range:'1h',markers,decimals:1});
 draw('incidentErrors',[{name:'Error Count',unit:'',points:pts(v=>v.hashrateMonitor?.asics?.[0]?.errorCount),color:'#b986ff',width:2.5}],{start:w.start,end:w.end,range:'1h',markers,decimals:0});
}
async function incidents(){let r=await fetch('/api/incidents'),x=await r.json();$('incidents').innerHTML=x.length?x.map(i=>{let end=i.ended_at||Math.floor(Date.now()/1000),b=i.before_sample||{},f=i.facts||{},fx=f.forensics||{},domains=f.domains,extra=domains?.length?' · Domains '+domains.map(v=>fmt(v,0)).join(' / '):'',errors=fx.error_count==null?'':' · Errors '+Number(fx.error_count).toLocaleString('de-DE')+(fx.error_count_delta==null?'':' (+'+Number(fx.error_count_delta).toLocaleString('de-DE')+')');return '<div class="event clickable" onclick="incidentDetail('+i.id+')"><span>'+new Date(i.started_at*1000).toLocaleString('de-DE')+'<br><small>'+dur(end-i.started_at)+'</small></span><b class="sev-'+i.severity+'">'+i.kind+'</b><span>'+i.summary+'<br><small>Vorher: '+fmt(b.voltage/1000,2)+' V · '+fmt(b.power,1)+' W · '+fmt(b.hashRate/1000,2)+' TH/s'+extra+errors+'</small></span></div>'}).join(''):'<div class="sub" style="padding-top:12px">Keine Vorfälle</div>'}
async function autoRestartStatus(){let r=await fetch('/api/settings/auto-restart'),a=await r.json(),t=$('autoRestartToggle');t.checked=!!a.enabled;t.disabled=false;$('autoRestartState').textContent=a.enabled?'EIN':'AUS';$('autoRestartState').style.color=a.enabled?'var(--green)':'var(--muted)'}
$('autoRestartToggle').onchange=async e=>{let t=e.currentTarget,w=t.checked;t.disabled=true;$('autoRestartState').textContent='…';try{let r=await fetch('/api/settings/auto-restart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:w})});if(!r.ok)throw new Error();await autoRestartStatus();await current();await events()}catch(err){t.checked=!w;t.disabled=false;$('autoRestartState').textContent='FEHLER';$('autoRestartState').style.color='var(--red)'}};
async function profileStatus(){let r=await fetch('/api/settings/mining-profile'),x=await r.json(),p=x.profiles||{},c=x.current||{};$('profileActive').textContent=x.active?p[x.active].label:'Benutzerdefiniert';$('profileCurrent').textContent=(c.frequency??'—')+' MHz · '+(c.coreVoltage??'—')+' mV · Lüfter '+(c.autofanspeed?'AUTO':(c.fanspeed??'—')+' %');document.querySelectorAll('#profileButtons button').forEach(b=>{let v=p[b.dataset.profile]||{},tip=(v.label||b.textContent)+': '+(v.frequency??'—')+' MHz · '+(v.coreVoltage??'—')+' mV · Lüfter '+(v.autofanspeed?'Auto':'fix '+(v.fanspeed??'—')+' %')+(v.custom?' · Benutzerdefinierte OC-Werte':'');b.classList.toggle('active',b.dataset.profile===x.active);b.title=tip;b.setAttribute('aria-label',tip)})}
document.querySelectorAll('#profileButtons button').forEach(b=>b.onclick=async()=>{let key=b.dataset.profile,custom=key==='oc'||key==='performance',message=custom?'Dieses Profil verwendet benutzerdefinierte OC-Werte außerhalb der vom Gamma angebotenen Standardauswahl. Höhere Leistung kann Netzteil, Spannungswandler und ASIC stärker belasten. Profil wirklich mit fester Lüfterleistung von 100 % aktivieren?':'Profilwerte mit fester Lüfterleistung von 100 % gemeinsam übernehmen?';if(!confirm(message))return;document.querySelectorAll('#profileButtons button').forEach(x=>x.disabled=true);$('profileHint').textContent='Profilwerte und 100 % Lüfterleistung werden an AxeOS übertragen …';try{let r=await fetch('/api/settings/mining-profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile:key,confirmed:true})}),x=await r.json();if(!r.ok)throw new Error(x.error||'Profilwechsel fehlgeschlagen');$('profileHint').textContent='Profil gespeichert – Lüfter fest auf 100 %, kein Neustart erforderlich.';setTimeout(profileStatus,1500)}catch(e){$('profileHint').textContent=e.message}finally{document.querySelectorAll('#profileButtons button').forEach(x=>x.disabled=false)}});
const widgetMeta=[['hashrate','Hashrate'],['power','Leistung'],['temperatures','ASIC / VR'],['shares','Shares'],['pool','Pool / Fehler'],['uptime','Laufzeit'],['health','Health & Gerätestatus'],['mining-profile','Mining-Profil'],['bitcoin','Bitcoin & Blockwert'],['public-pool','Mein Public-Pool-Miner'],['hashrate-chart','Hashrate-Chart'],['thermal-chart','Leistung & Temperatur'],['incidents','Incidents'],['events','Ereignisse']];let savedLayouts=[],layoutDirty=false,draggedWidget=null;
function layoutSnapshot(){return [...document.querySelector('.grid').children].filter(c=>c.classList.contains('card')).map(c=>({id:c.dataset.widget,collapsed:c.classList.contains('is-collapsed'),hidden:c.classList.contains('is-hidden')}))}
function setLayoutDirty(value=true){layoutDirty=value;let editing=document.querySelector('.grid').classList.contains('layout-edit'),named=!!$('layoutSelect')?.value;$('layoutDirty').textContent=value?(named?'Ungespeicherte Änderungen':'Ungespeicherter Entwurf – zum Behalten als neues Layout speichern'):'';$('layoutUpdate').hidden=!editing||!named;$('layoutSave').hidden=!editing}
function syncWidgetVisibility(card){card.hidden=card.classList.contains('is-hidden')&&!card.closest('.grid')?.classList.contains('layout-edit')}function applyLayout(layout){let grid=document.querySelector('.grid'),byId=Object.fromEntries([...grid.children].filter(c=>c.dataset.widget).map(c=>[c.dataset.widget,c]));layout.forEach(item=>{let card=byId[item.id];if(!card)return;card.classList.toggle('is-collapsed',!!item.collapsed);card.classList.toggle('is-hidden',!!item.hidden);let collapse=card.querySelector('[data-action="collapse"]'),hide=card.querySelector('[data-action="hide"]');if(collapse)collapse.textContent=item.collapsed?'Öffnen':'Minimieren';if(hide)hide.textContent=item.hidden?'Einblenden':'Ausblenden';grid.appendChild(card)});[...grid.children].forEach(syncWidgetVisibility);setLayoutDirty(false);setTimeout(()=>history(document.querySelector('.tabs[data-chart] button.active')?.dataset.r||'1h'),0)}
function defaultLayout(){return widgetMeta.map(([id])=>({id,collapsed:false,hidden:false}))}function leaveEdit(){let grid=document.querySelector('.grid');grid.classList.remove('layout-edit');[...grid.children].forEach(c=>{c.draggable=false;syncWidgetVisibility(c)});$('layoutEdit').textContent='Anpassen';$('layoutUpdate').hidden=true;$('layoutSave').hidden=true}function enterEdit(){let grid=document.querySelector('.grid');grid.classList.add('layout-edit');[...grid.children].filter(c=>c.dataset.widget).forEach(c=>{c.hidden=false;c.draggable=true});$('layoutEdit').textContent='Bearbeitung beenden';setLayoutDirty(layoutDirty)}
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');async function reloadLayouts(selectName=''){let r=await fetch('/api/layouts'),x=await r.json();savedLayouts=Array.isArray(x)?x:[];$('layoutSelect').innerHTML='<option value="">Standardlayout (geschützt)</option>'+savedLayouts.map(l=>'<option value="'+esc(l.name)+'">'+esc(l.name)+'</option>').join('');$('layoutSelect').value=selectName}
async function setupLayouts(){let grid=document.querySelector('.grid'),cards=[...grid.children].filter(c=>c.classList.contains('card'));grid.id='dashboardGrid';let bar=document.createElement('div');bar.className='layoutbar';bar.innerHTML='<span class="label">Dashboard-Layout</span><select id="layoutSelect"><option value="">Standardlayout (geschützt)</option></select><button id="layoutEdit">Anpassen</button><button id="layoutUpdate" hidden>Änderungen speichern</button><button id="layoutSave" hidden>Als neues Layout speichern</button><button id="layoutDefault">Standard wiederherstellen</button><span class="layoutdirty" id="layoutDirty"></span>';grid.before(bar);cards.forEach((card,index)=>{let [id,title]=widgetMeta[index];card.dataset.widget=id;let body=document.createElement('div');body.className='widget-body';while(card.firstChild)body.appendChild(card.firstChild);let controls=document.createElement('div');controls.className='widgetbar';controls.innerHTML='<b>↕ '+title+'</b><button type="button" data-action="collapse">Minimieren</button><button type="button" data-action="hide">Ausblenden</button>';card.append(controls,body);controls.onclick=e=>{let action=e.target.dataset.action;if(!action)return;if(action==='collapse'){card.classList.toggle('is-collapsed');e.target.textContent=card.classList.contains('is-collapsed')?'Öffnen':'Minimieren'}else{card.classList.toggle('is-hidden');e.target.textContent=card.classList.contains('is-hidden')?'Einblenden':'Ausblenden'}setLayoutDirty()};card.ondragstart=()=>{if(!grid.classList.contains('layout-edit'))return false;draggedWidget=card;card.classList.add('dragging')};card.ondragend=()=>{card.classList.remove('dragging');[...grid.children].forEach(c=>c.classList.remove('dragover'));draggedWidget=null};card.ondragover=e=>{if(draggedWidget&&draggedWidget!==card){e.preventDefault();card.classList.add('dragover')}};card.ondragleave=()=>card.classList.remove('dragover');card.ondrop=e=>{e.preventDefault();card.classList.remove('dragover');if(draggedWidget&&draggedWidget!==card){let rect=card.getBoundingClientRect();grid.insertBefore(draggedWidget,e.clientY<rect.top+rect.height/2?card:card.nextSibling);setLayoutDirty()}}});await reloadLayouts(localStorage.getItem('bitaxeLayout')||'');let selected=$('layoutSelect').value,stored=savedLayouts.find(l=>l.name===selected);applyLayout(stored?.layout||defaultLayout());$('layoutSelect').onchange=e=>{if(layoutDirty&&!confirm('Ungespeicherte Änderungen verwerfen?')){e.target.value=localStorage.getItem('bitaxeLayout')||'';return}let item=savedLayouts.find(l=>l.name===e.target.value);applyLayout(item?.layout||defaultLayout());localStorage.setItem('bitaxeLayout',e.target.value);leaveEdit()};$('layoutEdit').onclick=()=>grid.classList.contains('layout-edit')?leaveEdit():enterEdit();$('layoutDefault').onclick=()=>{if(layoutDirty&&!confirm('Entwurf verwerfen und Standardlayout wiederherstellen?'))return;applyLayout(defaultLayout());$('layoutSelect').value='';localStorage.removeItem('bitaxeLayout');leaveEdit()};$('layoutUpdate').onclick=async()=>{let name=$('layoutSelect').value;if(!name)return;let r=await fetch('/api/layouts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,layout:layoutSnapshot(),overwrite:true})}),x=await r.json();if(!r.ok){alert(x.error||'Layout konnte nicht aktualisiert werden.');return}await reloadLayouts(x.name);localStorage.setItem('bitaxeLayout',x.name);setLayoutDirty(false);leaveEdit()};$('layoutSave').onclick=async()=>{let name=prompt('Name für das neue Layout:','');if(name==null)return;name=name.trim();if(!name){alert('Zum Speichern ist ein eigener Layoutname erforderlich.');return}let r=await fetch('/api/layouts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,layout:layoutSnapshot()})}),x=await r.json();if(!r.ok){alert(x.error||'Layout konnte nicht gespeichert werden.');return}await reloadLayouts(x.name);localStorage.setItem('bitaxeLayout',x.name);setLayoutDirty(false);leaveEdit()}}
async function refreshDashboard(){nextRefreshAt=Date.now()+REFRESH_MS;try{await Promise.all([current(),autoRestartStatus(),profileStatus()]);markRefresh()}catch(e){refreshClock()}}document.querySelectorAll('.tabs[data-chart] button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs[data-chart] button').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.tabs[data-chart] button[data-r="'+b.dataset.r+'"]').forEach(x=>x.classList.add('active'));history(b.dataset.r)});setupLayouts();refreshDashboard();market();history();events();incidents();setInterval(()=>{refreshDashboard();history(document.querySelector('.tabs[data-chart] button.active').dataset.r);events();incidents()},REFRESH_MS);setInterval(refreshClock,1000);setInterval(market,60000);
</script></body></html>'''


def now():
    return int(time.time())


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def migrate_schema(con):
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)")
    applied = {row[0] for row in con.execute("SELECT version FROM schema_migrations")}
    if 2 not in applied:
        columns = {row[1] for row in con.execute("PRAGMA table_info(events)")}
        if "incident_id" not in columns:
            con.execute("ALTER TABLE events ADD COLUMN incident_id INTEGER")
        if "automatic" not in columns:
            con.execute("ALTER TABLE events ADD COLUMN automatic INTEGER NOT NULL DEFAULT 0")
        if "details" not in columns:
            con.execute("ALTER TABLE events ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS incident_diagnostics (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          incident_id INTEGER NOT NULL, captured_at INTEGER NOT NULL,
          source_status TEXT NOT NULL, observed_json TEXT NOT NULL,
          derived_json TEXT NOT NULL, raw_field_names_json TEXT NOT NULL,
          FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_incident_diagnostics_incident
          ON incident_diagnostics(incident_id,captured_at);
        """)
        con.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(2,?)", (now(),))
    if 3 not in applied:
        con.execute("""CREATE TABLE IF NOT EXISTS dashboard_layouts(
            name TEXT PRIMARY KEY COLLATE NOCASE, layout_json TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
        con.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(3,?)", (now(),))


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS samples (
          ts INTEGER PRIMARY KEY, power REAL, voltage REAL, current REAL, temp REAL,
          vr_temp REAL, core_voltage REAL, frequency REAL, hashrate REAL,
          hashrate_1m REAL, hashrate_10m REAL, hashrate_1h REAL, error_pct REAL,
          accepted INTEGER, rejected INTEGER, best_diff REAL, best_session_diff REAL,
          pool_diff REAL, network_diff REAL, response_ms REAL, uptime INTEGER,
          total_uptime INTEGER, fan_rpm INTEGER, wifi_rssi INTEGER, fallback INTEGER,
          block_found INTEGER, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL,
          severity TEXT NOT NULL, message TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
        CREATE TABLE IF NOT EXISTS incidents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at INTEGER NOT NULL, ended_at INTEGER, status TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'UNKNOWN', severity TEXT NOT NULL DEFAULT 'warning',
          title TEXT NOT NULL, summary TEXT NOT NULL, observed_cause TEXT,
          recovery TEXT, facts TEXT NOT NULL DEFAULT '{}',
          before_sample TEXT, after_sample TEXT, pre_stats TEXT,
          created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_incidents_started ON incidents(started_at DESC);
        CREATE TABLE IF NOT EXISTS incident_samples (
          incident_id INTEGER NOT NULL, ts INTEGER NOT NULL, phase TEXT NOT NULL,
          payload TEXT NOT NULL, PRIMARY KEY(incident_id,ts),
          FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS monitor_state (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        """)
        migrate_schema(con)


def merge_intervals(intervals, start, end):
    clipped = sorted((max(start, a), min(end, b)) for a, b in intervals if b > start and a < end)
    merged = []
    for left, right in clipped:
        if not merged or left > merged[-1][1]:
            merged.append([left, right])
        else:
            merged[-1][1] = max(merged[-1][1], right)
    return [tuple(value) for value in merged]


def interval_seconds(intervals, start, end):
    return sum(right - left for left, right in merge_intervals(intervals, start, end))


def interval_overlap_seconds(intervals_a, intervals_b, start, end):
    left_values = merge_intervals(intervals_a, start, end)
    right_values = merge_intervals(intervals_b, start, end)
    total = left_index = right_index = 0
    while left_index < len(left_values) and right_index < len(right_values):
        left_a, right_a = left_values[left_index]
        left_b, right_b = right_values[right_index]
        total += max(0, min(right_a, right_b) - max(left_a, left_b))
        if right_a <= right_b:
            left_index += 1
        else:
            right_index += 1
    return total


def time_weighted_hashrate(samples, start, end, offline_intervals=(), carry_seconds=None,
                           excluded_intervals=()):
    """Integrate observed GH/s over covered time; confirmed downtime contributes zero."""
    carry = carry_seconds or POLL_SECONDS * 3
    samples = sorted((int(ts), float(rate or 0)) for ts, rate in samples if ts <= end)
    offline = merge_intervals(offline_intervals, start, end)
    measured = []
    for index, (stamp, rate) in enumerate(samples):
        left = max(start, stamp)
        next_stamp = samples[index + 1][0] if index + 1 < len(samples) else end
        right = min(end, next_stamp, stamp + carry)
        if right > left:
            measured.append((left, right, rate))
    measured_seconds = sum(right - left for left, right, _ in measured)
    offline_seconds = sum(right - left for left, right in offline)
    work = sum((right - left) * rate for left, right, rate in measured)
    overlap_seconds = 0
    measured_index = offline_index = 0
    while measured_index < len(measured) and offline_index < len(offline):
        measured_left, measured_right, rate = measured[measured_index]
        offline_left, offline_right = offline[offline_index]
        overlap = max(0, min(measured_right, offline_right) - max(measured_left, offline_left))
        if overlap:
            overlap_seconds += overlap
            work -= overlap * rate
        if measured_right <= offline_right:
            measured_index += 1
        else:
            offline_index += 1
    covered_intervals = [(left, right) for left, right, _ in measured] + offline
    excluded_covered = interval_overlap_seconds(covered_intervals, excluded_intervals, start, end)
    blocked = list(offline) + list(excluded_intervals)
    work = sum(((measured_right - measured_left) - interval_overlap_seconds(
        [(measured_left, measured_right)], blocked, start, end)) * rate
        for measured_left, measured_right, rate in measured)
    covered = measured_seconds + offline_seconds - overlap_seconds - excluded_covered
    return {"average": None if not covered else work / covered, "coverage": int(covered)}


def planned_pause_intervals(con, start, end):
    intervals = []
    open_since = None
    for event in con.execute("""SELECT ts,kind FROM events
        WHERE kind IN ('USER_PAUSED','USER_RESUMED') AND ts<=? ORDER BY ts,id""", (end,)):
        if event["kind"] == "USER_PAUSED":
            open_since = event["ts"] if open_since is None else open_since
        elif open_since is not None:
            intervals.append((open_since, event["ts"]))
            open_since = None
    if open_since is not None:
        intervals.append((open_since, end))
    return merge_intervals(intervals, start, end)


def confirmed_downtime(con, start, end):
    kinds = ("POWER_INTERRUPTION", "MINING_STALL", "SOFTWARE_RESTART",
             "NETWORK_OR_API_OUTAGE", "THERMAL_EVENT", "UNKNOWN")
    placeholders = ",".join("?" for _ in kinds)
    rows = con.execute(f"""SELECT started_at,COALESCE(ended_at,?) ended_at FROM incidents
        WHERE kind IN ({placeholders}) AND started_at<? AND COALESCE(ended_at,?)>?""",
        (end, *kinds, end, end, start)).fetchall()
    intervals = [(row["started_at"], row["ended_at"]) for row in rows]
    open_since = None
    for event in con.execute("""SELECT ts,kind FROM events WHERE kind IN ('OFFLINE','RECOVERED')
        AND ts<=? ORDER BY ts""", (end,)):
        if event["kind"] == "OFFLINE":
            open_since = event["ts"]
        elif open_since is not None:
            intervals.append((open_since, event["ts"]))
            open_since = None
    if open_since is not None:
        intervals.append((open_since, end))
    return merge_intervals(intervals, start, end)


def historical_hashrate(at=None):
    end = int(at or now())
    result = {}
    with db() as con:
        first = con.execute("SELECT MIN(ts) FROM samples").fetchone()[0]
        for label, seconds in (("1h", 3600), ("24h", 86400), ("7d", 604800)):
            start = end - seconds
            query_start = start - POLL_SECONDS * 3
            rows = con.execute("SELECT ts,hashrate FROM samples WHERE ts>=? AND ts<=? ORDER BY ts",
                               (query_start, end)).fetchall()
            downtime = confirmed_downtime(con, start, end)
            pauses = planned_pause_intervals(con, start, end)
            measured_start = max(start, first) if first is not None else end
            calculation = time_weighted_hashrate(
                [(row["ts"], row["hashrate"]) for row in rows], measured_start, end,
                downtime, POLL_SECONDS * 3, pauses)
            coverage = calculation["coverage"]
            pause_seconds = interval_seconds(pauses, measured_start, end)
            eligible_seconds = max(0, seconds - pause_seconds)
            pause_count = con.execute("SELECT COUNT(*) FROM events WHERE kind='USER_PAUSED' AND ts BETWEEN ? AND ?",
                                      (start, end)).fetchone()[0]
            result[f"avg_{label}"] = calculation["average"]
            result[f"coverage_{label}"] = coverage
            result[f"coverage_pct_{label}"] = min(100.0, coverage * 100 / eligible_seconds) if eligible_seconds else 100.0
            result[f"planned_pause_seconds_{label}"] = pause_seconds
            result[f"user_pauses_{label}"] = pause_count
            result[f"complete_{label}"] = coverage >= eligible_seconds - POLL_SECONDS * 3
    return result


def cached_historical_hashrate(at=None):
    stamp = int(at or now())
    with hashrate_lock:
        if stamp - hashrate_cache["updated"] >= 60 or not hashrate_cache["data"]:
            hashrate_cache["data"] = historical_hashrate(stamp)
            hashrate_cache["updated"] = stamp
        return dict(hashrate_cache["data"])


def calculated_current(data):
    """Return input current derived from watts and input volts; never guess API units."""
    power, millivolts = data.get("power"), data.get("voltage")
    try:
        volts = float(millivolts) / 1000
        return float(power) / volts if volts > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def mining_efficiency(power_w, hashrate_gh):
    """Return J/TH from observed watts and GH/s, or None for invalid input."""
    try:
        power = float(power_w)
        terahash = float(hashrate_gh) / 1000.0
        return power / terahash if power >= 0 and terahash > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def safe_payload(data):
    payload = dict(data or {})
    payload["calculatedCurrent"] = calculated_current(payload)
    payload["efficiencyJTh"] = mining_efficiency(payload.get("power"), payload.get("hashRate"))
    return payload


def error_count(data):
    asics = ((data or {}).get("hashrateMonitor") or {}).get("asics") or []
    value = asics[0].get("errorCount") if asics else None
    return value if isinstance(value, (int, float)) else None


def stable_error_baseline(started_at):
    """Last healthy pre-incident counter; counter resets are handled by the caller."""
    with db() as con:
        rows = con.execute("SELECT payload FROM samples WHERE ts<? ORDER BY ts DESC LIMIT 60",
                           (started_at,)).fetchall()
    for row in rows:
        sample = json.loads(row["payload"])
        domains = (((sample.get("hashrateMonitor") or {}).get("asics") or [{}])[0].get("domains"))
        if (sample.get("hashRate") or 0) > 0 and domains and all(
                isinstance(value, (int, float)) and value > 1 for value in domains):
            value = error_count(sample)
            if value is not None:
                return value
    return None


def incident_metrics(started_at, snapshot=None):
    """Neutral, database-backed pre-incident measurements and counter delta."""
    current = safe_payload(snapshot or {})
    with db() as con:
        rows = con.execute("SELECT ts,voltage,power,temp,vr_temp,payload FROM samples "
                           "WHERE ts BETWEEN ? AND ? ORDER BY ts",
                           (started_at - 300, started_at)).fetchall()
    def minimum(field, seconds):
        values = [row[field] for row in rows
                  if row["ts"] >= started_at - seconds and isinstance(row[field], (int, float))]
        return min(values) if values else None
    observed_error = error_count(current)
    asics = ((current.get("hashrateMonitor") or {}).get("asics") or [])
    domains = asics[0].get("domains") if asics else None
    baseline_error = stable_error_baseline(started_at)
    delta = (observed_error - baseline_error
             if observed_error is not None and baseline_error is not None
             and observed_error >= baseline_error else None)
    return {
        "voltage_incident": current.get("voltage"),
        "voltage_min_60s": minimum("voltage", 60),
        "voltage_min_5m": minimum("voltage", 300),
        "power_min_60s": minimum("power", 60),
        "power_min_5m": minimum("power", 300),
        "temp_min_60s": minimum("temp", 60),
        "temp_min_5m": minimum("temp", 300),
        "vr_temp_min_60s": minimum("vr_temp", 60),
        "vr_temp_min_5m": minimum("vr_temp", 300),
        "error_count": observed_error,
        "error_count_baseline": baseline_error,
        "error_count_delta": delta,
        "domains": list(domains) if isinstance(domains, list) else None,
    }


def voltage_summary(hours=24, at=None):
    end = int(at or now())
    with db() as con:
        row = con.execute("SELECT MIN(voltage),AVG(voltage),MAX(voltage) FROM samples "
                          "WHERE ts BETWEEN ? AND ? AND voltage IS NOT NULL",
                          (end - hours * 3600, end)).fetchone()
    return {"min": row[0], "avg": row[1], "max": row[2]} if row and row[0] is not None else {}


def incident_window(started_at, ended_at=None, at=None):
    return {"start": int(started_at) - 300,
            "end": int(ended_at or at or now()) + 300}


def pre_crash_snapshot(ts, seconds=300):
    with db() as con:
        rows = con.execute("SELECT ts,payload FROM samples WHERE ts>=? AND ts<? ORDER BY ts",
                           (ts - seconds, ts)).fetchall()
    samples = [safe_payload(json.loads(r["payload"])) | {"ts": r["ts"]} for r in rows]
    fields = ("hashRate", "hashRate_1m", "hashRate_10m", "hashRate_1h", "power",
              "voltage", "calculatedCurrent", "temp", "vrTemp", "coreVoltageActual",
              "actualFrequency", "fanrpm", "fanSpeed", "errorPercentage", "wifiRSSI",
              "responseTime")
    stats = {}
    for field in fields:
        values = [s[field] for s in samples if isinstance(s.get(field), (int, float))]
        if values:
            stats[field] = {"min": min(values), "max": max(values), "avg": sum(values) / len(values)}
    return samples[-1] if samples else None, stats


def create_incident(started_at, kind="UNKNOWN", title="Vorfall erkannt", summary="Ursache nicht eindeutig",
                    severity="warning", observed=None, facts=None, before_override=None):
    before, stats = pre_crash_snapshot(started_at)
    before = before_override or before
    stamp = now()
    with db() as con:
        cur = con.execute("""INSERT INTO incidents
            (started_at,status,kind,severity,title,summary,observed_cause,facts,before_sample,pre_stats,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (started_at, "ACTIVE", kind, severity, title, summary, observed,
             json.dumps(facts or {}, separators=(",", ":")),
             json.dumps(before, separators=(",", ":")) if before else None,
             json.dumps(stats, separators=(",", ":")), stamp, stamp))
        incident_id = cur.lastrowid
        for sample in get_sample_window(started_at - 300, started_at - 1):
            con.execute("INSERT OR IGNORE INTO incident_samples VALUES(?,?,?,?)",
                        (incident_id, sample["ts"], "before", json.dumps(sample, separators=(",", ":"))))
    return incident_id


def update_incident(incident_id, **changes):
    allowed = {"ended_at", "status", "kind", "severity", "title", "summary",
               "observed_cause", "recovery", "facts", "after_sample"}
    values, clauses = [], []
    for key, value in changes.items():
        if key not in allowed:
            continue
        if key in {"facts", "after_sample"} and value is not None:
            value = json.dumps(value, separators=(",", ":"))
        clauses.append(f"{key}=?")
        values.append(value)
    clauses.append("updated_at=?")
    values.extend((now(), incident_id))
    with db() as con:
        con.execute(f"UPDATE incidents SET {','.join(clauses)} WHERE id=?", values)


def confirm_historical_user_pause(incident_id):
    """Reclassify a user-confirmed legacy incident without altering telemetry."""
    with db() as con:
        row = con.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            return None
        start = int(row["started_at"])
        end = int(row["ended_at"] or start)
        facts = json.loads(row["facts"] or "{}")
        facts.update({"planned": True, "user_confirmed": True,
                      "reclassified_from": row["kind"]})
        con.execute("""UPDATE incidents SET kind='USER_PAUSED', severity='info',
            title='USER PAUSED', summary='Vom Benutzer bestätigte geplante Mining-Pause',
            observed_cause='Benutzerpause', status='PLANNED', facts=?, updated_at=? WHERE id=?""",
                    (json.dumps(facts, separators=(",", ":")), now(), incident_id))
        for ts, kind, message in (
            (start, "USER_PAUSED", "Historische Mining-Pause vom Benutzer bestätigt."),
            (end, "USER_RESUMED", "Mining nach bestätigter Benutzerpause fortgesetzt."),
        ):
            exists = con.execute("SELECT 1 FROM events WHERE kind=? AND ts=?", (kind, ts)).fetchone()
            if not exists:
                con.execute("""INSERT INTO events(ts,kind,severity,message,incident_id,automatic,details)
                    VALUES(?,?,?,?,?,0,?)""", (ts, kind, "info", message, incident_id,
                    json.dumps({"planned": True, "user_confirmed": True,
                                "historical_reclassification": True}, separators=(",", ":"))))
        return {"id": incident_id, "started_at": start, "ended_at": end,
                "kind": "USER_PAUSED", "status": "PLANNED"}


def persisted_incident_forensics(incident_id):
    with db() as con:
        row = con.execute("SELECT facts FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if not row or not row["facts"]:
        return None
    return (json.loads(row["facts"]) or {}).get("forensics")


def attach_incident_sample(incident_id, ts, phase, data):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO incident_samples VALUES(?,?,?,?)",
                    (incident_id, ts, phase, json.dumps(safe_payload(data), separators=(",", ":"))))


def get_sample_window(start, end):
    with db() as con:
        rows = con.execute("SELECT ts,payload FROM samples WHERE ts BETWEEN ? AND ? ORDER BY ts",
                           (start, end)).fetchall()
    return [safe_payload(json.loads(r["payload"])) | {"ts": r["ts"]} for r in rows]


def chart_history(start, end, bucket):
    """Return timestamped aggregates and explicit gaps without turning low values into gaps."""
    with db() as con:
        rows = con.execute("""SELECT CAST(AVG(ts) AS INTEGER) ts,
            AVG(hashrate) hashrate,AVG(power) power,AVG(temp) temp,
            AVG(voltage)/1000.0 voltage,AVG(vr_temp) vr_temp,
            AVG(core_voltage) core_voltage,AVG(frequency) frequency,
            AVG(fan_rpm) fan_rpm,AVG(response_ms) response_ms,
            MAX(accepted) accepted,MAX(rejected) rejected
            FROM samples WHERE ts BETWEEN ? AND ? GROUP BY CAST(ts/? AS INTEGER) ORDER BY ts""",
            (start, end, bucket)).fetchall()
    result = []
    gap_after = max(POLL_SECONDS * 3, int(bucket * 2.5))
    previous_ts = None
    for row in rows:
        item = dict(row)
        if previous_ts is not None and item["ts"] - previous_ts > gap_after:
            result.append({"ts": previous_ts + 1, "gap": True})
            result.append({"ts": item["ts"] - 1, "gap": True})
        result.append(item)
        previous_ts = item["ts"]
    return result


def chart_markers(start, end):
    with db() as con:
        incidents = con.execute("""SELECT id,started_at ts,ended_at,kind,summary description,0 automatic
            FROM incidents WHERE started_at<=? AND COALESCE(ended_at,?)>=?""", (end, end, start)).fetchall()
        events = con.execute("""SELECT id,ts,NULL ended_at,kind,message description,automatic
            FROM events WHERE ts BETWEEN ? AND ? AND kind IN
            ('ASIC_DOMAIN_STALL_DETECTED','HASHRATE_DROP_DETECTED','AUTO_RECOVERY_RESTART','AUTO_RECOVERY_SUPPRESSED','POWER_FAULT',
             'OFFLINE','REBOOT','RECOVERED','POOL_OR_STRATUM_ISSUE','THERMAL_EVENT')""",
            (start, end)).fetchall()
        pauses = planned_pause_intervals(con, start, end)
    pause_markers = [{"id": None, "ts": left, "ended_at": right, "kind": "USER_PAUSED",
                      "description": "Bewusste Mining-Pause", "automatic": 0, "planned": True}
                     for left, right in pauses]
    return sorted([dict(row) for row in (*incidents, *events)] + pause_markers,
                  key=lambda item: item["ts"])


def backfill_historical_incidents(days=30):
    """Create evidence-only incidents from existing raw samples once after upgrading."""
    with db() as con:
        if con.execute("SELECT 1 FROM monitor_state WHERE key='backfill_v11_1'").fetchone():
            return
        rows = con.execute("SELECT ts,payload FROM samples WHERE ts>=? ORDER BY ts",
                           (now() - days * 86400,)).fetchall()
    run = []
    before = None
    last_normal = None

    def finish(after=None):
        nonlocal run, before
        if len(run) >= STALL_AFTER_POLLS and run[-1]["ts"] - run[0]["ts"] >= POLL_SECONDS * 2:
            first, last = run[0], run[-1]
            with db() as con:
                exists = con.execute("SELECT id FROM incidents WHERE started_at BETWEEN ? AND ?",
                                     (first["ts"] - POLL_SECONDS, first["ts"] + POLL_SECONDS)).fetchone()
            kind, reason = classify_incident(before, first, after or {})
            if not exists:
                incident_id = create_incident(first["ts"], kind, kind.replace("_", " "),
                                              reason or "Ursache nicht eindeutig", "warning",
                                              observed_cause(first), {"historical_reconstruction": True}, before)
            else:
                incident_id = exists["id"]
            for sample in run:
                attach_incident_sample(incident_id, sample["ts"], "during", sample)
            recovery = "Mining in gespeicherten Messwerten wieder aktiv" if after else "Ende nicht beobachtet"
            update_incident(incident_id, ended_at=(after or last)["ts"], status="RESOLVED" if after else "UNKNOWN",
                            kind=kind, title=kind.replace("_", " "), summary=reason or "Ursache nicht eindeutig",
                            recovery=recovery, after_sample=after,
                            facts={"historical_reconstruction": True})
        run = []
        before = None

    for row in rows:
        sample = safe_payload(json.loads(row["payload"])) | {"ts": row["ts"]}
        stopped = (sample.get("hashRate") or 0) <= 10 and not sample.get("miningPaused")
        if stopped:
            if not run:
                before = last_normal
            run.append(sample)
        else:
            if run:
                finish(sample)
            last_normal = sample
    if run:
        finish()
    with db() as con:
        con.execute("INSERT OR REPLACE INTO monitor_state(key,value) VALUES('backfill_v11_1',?)", (str(now()),))


def add_event(kind, severity, message, ts=None, incident_id=None, automatic=False, details=None, con=None):
    values = (ts or now(), kind, severity, message, incident_id, int(bool(automatic)),
              json.dumps(details or {}, separators=(",", ":")))
    if con is not None:
        return con.execute("""INSERT INTO events
            (ts,kind,severity,message,incident_id,automatic,details) VALUES(?,?,?,?,?,?,?)""", values).lastrowid
    with db() as connection:
        return connection.execute("""INSERT INTO events
            (ts,kind,severity,message,incident_id,automatic,details) VALUES(?,?,?,?,?,?,?)""", values).lastrowid


def update_event(event_id, kind, severity, message):
    with db() as con:
        con.execute("UPDATE events SET kind=?,severity=?,message=? WHERE id=?",
                    (kind, severity, message, event_id))


def domain_register_paths(data):
    paths = {}
    for asic_index, asic in enumerate(((data or {}).get("hashrateMonitor") or {}).get("asics") or []):
        for domain_index, value in enumerate(asic.get("domains") or []):
            paths[f"hashrateMonitor.asics[{asic_index}].domains[{domain_index}]"] = value
    return paths


def domain_stall_evidence(data):
    """Return directly observed stalled domains when acting is otherwise safe."""
    asics = ((data or {}).get("hashrateMonitor") or {}).get("asics") or []
    domains = asics[0].get("domains") if asics else None
    if not isinstance(domains, list) or len(domains) < 4:
        return None
    numeric = [float(value) if isinstance(value, (int, float)) else 0.0 for value in domains]
    stalled = [index for index, value in enumerate(numeric) if value <= 1.0]
    safe = (
        len(stalled) >= 2
        and (data.get("uptimeSeconds") or 0) >= AUTO_RESTART_MIN_UPTIME
        and (data.get("power") or 0) > IDLE_POWER_W
        and (data.get("actualFrequency") or 0) > 0
        and not data.get("miningPaused")
        and not data.get("isUsingFallbackStratum")
        and not data.get("overheat_mode")
        and not data.get("power_fault")
        and not data.get("hardware_fault")
    )
    return {"domains": numeric, "stalled_indexes": stalled,
            "active_count": len(numeric) - len(stalled)} if safe else None


def domains_recovered(data):
    asics = ((data or {}).get("hashrateMonitor") or {}).get("asics") or []
    domains = asics[0].get("domains") if asics else None
    return bool(isinstance(domains, list) and len(domains) >= 4
                and all(isinstance(value, (int, float)) and value > 1 for value in domains))


def diagnostic_snapshot(data):
    observed = safe_payload(clean(data or {}))
    raw_current = observed.get("current")
    derived = {"calculatedInputCurrent": calculated_current(observed)}
    field_paths = domain_register_paths(observed)
    return {
        "observed": observed,
        "derived": derived,
        "field_names": {"rawCurrent": "current", "domain_register_values": field_paths},
        "semantics": {"current": "raw AxeOS value; unit not inferred",
                      "calculatedInputCurrent": "derived as power / input voltage",
                      "domain_register_values": "observed AxeOS hashrateMonitor domain/register values; not summed"},
    }


def persist_pre_restart_snapshot(incident_id, data, source_status, restart_details, captured_at=None):
    """Commit snapshot and restart event atomically; caller may restart only after return."""
    stamp = int(captured_at or now())
    snapshot = diagnostic_snapshot(data)
    with db() as con:
        con.execute("""INSERT INTO incident_diagnostics
            (incident_id,captured_at,source_status,observed_json,derived_json,raw_field_names_json)
            VALUES(?,?,?,?,?,?)""",
            (incident_id, stamp, source_status,
             json.dumps(snapshot["observed"], separators=(",", ":")),
             json.dumps(snapshot["derived"], separators=(",", ":")),
             json.dumps({"paths": snapshot["field_names"], "semantics": snapshot["semantics"]},
                        separators=(",", ":"))))
        add_event("AUTO_RECOVERY_RESTART", "warning", restart_details["reason"], stamp,
                  incident_id, True, restart_details, con)
    return snapshot


def automatic_restarts_since(since):
    with db() as con:
        return con.execute("""SELECT COUNT(*) FROM events
            WHERE kind='AUTO_RECOVERY_RESTART' AND automatic=1 AND ts>=?""", (since,)).fetchone()[0]


def fetch_bitaxe_info():
    req = urllib.request.Request(API_URL, headers={"Accept": "application/json", "User-Agent": "BitaxeMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=6) as res:
        return json.load(res)


def freshest_restart_evidence(fallback):
    """Best-effort final read. Never discard the last valid sample when it fails."""
    try:
        raw = fetch_bitaxe_info()
        return clean(raw), "fresh_api_read"
    except Exception as exc:
        return clean(fallback or {}), "fresh_api_read_failed:" + type(exc).__name__


def restart_with_persisted_evidence(incident_id, fallback, details, captured_at=None):
    """The restart request is deliberately unreachable until the DB transaction commits."""
    evidence, source_status = freshest_restart_evidence(fallback)
    if evidence.get("miningPaused"):
        stamp = int(captured_at or now())
        record_user_pause_transition({}, evidence, stamp)
        add_event("AUTO_RECOVERY_CANCELLED_USER_PAUSED", "info",
                  "Automatischer Neustart abgebrochen: Mining wurde über AxeOS pausiert",
                  stamp, automatic=True, details={"reason": "miningPaused=true"})
        return evidence, source_status, "cancelled_user_paused"
    persist_pre_restart_snapshot(incident_id, evidence, source_status, details, captured_at)
    try:
        request_axeos_restart()
        request_status = "accepted"
    except Exception as exc:
        # AxeOS commonly closes the connection while rebooting. Telemetry verifies the outcome.
        request_status = "connection_closed:" + type(exc).__name__
    return evidence, source_status, request_status


def clean(raw):
    data = {k: raw.get(k) for k in ALLOWED}
    monitor = raw.get("hashrateMonitor") or {}
    safe_asics = []
    for asic in monitor.get("asics") or []:
        domains = [value for value in (asic.get("domains") or []) if isinstance(value, (int, float))]
        safe_asics.append({"total": asic.get("total"), "errorCount": asic.get("errorCount"),
                           "domains": domains})
    data["hashrateMonitor"] = {"asics": safe_asics}
    return data


def mining_address(raw):
    value = str(raw.get("stratumUser") or "").split(".", 1)[0].strip()
    return value if value.startswith(("bc1", "1", "3")) else None


def get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "BitaxeMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)


def block_subsidy(height):
    halvings = max(0, int(height or 0) // 210000)
    return 50 / (2 ** halvings) if halvings < 64 else 0


def coinbase_block_value(data, height, btc_eur=None):
    """Return decoded AxeOS coinbase values without inferring pool-fee semantics."""
    def btc_from_satoshis(value):
        try:
            satoshis = int(value)
            return satoshis / 100_000_000 if satoshis > 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    subsidy = block_subsidy(height)
    total = btc_from_satoshis((data or {}).get("coinbaseValueTotalSatoshis"))
    user = btc_from_satoshis((data or {}).get("coinbaseValueUserSatoshis"))
    fees = max(0, total - subsidy) if total is not None else None
    miner = user if user is not None else subsidy
    return {
        "height": int(height or 0),
        "subsidy_btc": subsidy,
        "fees_btc": fees,
        "total_btc": total,
        "miner_btc": miner,
        "miner_eur": None if btc_eur is None else miner * float(btc_eur),
        "coinbase_available": user is not None,
    }


def summarize_candles(candles, cutoff):
    recent = sorted((row for row in candles if len(row) >= 5 and int(row[0]) >= cutoff),
                    key=lambda row: row[0])
    history = [{"ts": int(row[0]), "close": float(row[4])} for row in recent]
    first = history[0]["close"] if history else None
    change = ((history[-1]["close"] - first) * 100 / first if first else None)
    low = min((float(row[1]) for row in recent), default=None)
    high = max((float(row[2]) for row in recent), default=None)
    return history, change, low, high


def observed_cause(data):
    if data.get("power_fault"):
        return "Power Fault detected: " + str(data["power_fault"])
    if data.get("hardware_fault"):
        return "Hardware Fault detected: " + str(data["hardware_fault"])
    if data.get("overheat_mode"):
        return "Überhitzungsschutz aktiv"
    return None


def classify_incident(before, during, after=None, offline_seconds=0):
    """Classify only from correlated observations, never from a stale resetReason alone."""
    before, during, after = before or {}, during or {}, after or {}
    fault = observed_cause(during) or observed_cause(after)
    if (during.get("power_fault") or after.get("power_fault")):
        return "POWER_INTERRUPTION", fault
    if during.get("overheat_mode") or after.get("overheat_mode"):
        return "THERMAL_EVENT", fault
    old_uptime, new_uptime = before.get("uptimeSeconds"), after.get("uptimeSeconds")
    rebooted = isinstance(old_uptime, (int, float)) and isinstance(new_uptime, (int, float)) and new_uptime + 30 < old_uptime
    reset = str(after.get("resetReason") or "").lower()
    hash_stopped = (during.get("hashRate") or 0) <= 10
    idle_power = 0 < (during.get("power") or 0) <= IDLE_POWER_W
    uptime_continues = isinstance(old_uptime, (int, float)) and isinstance(during.get("uptimeSeconds"), (int, float)) and during["uptimeSeconds"] >= old_uptime
    if hash_stopped and idle_power and uptime_continues:
        return "MINING_STALL", "Controller erreichbar; Hashrate und ASIC-Leistung eingebrochen; Uptime lief weiter"
    if rebooted and any(word in reset for word in ("power-on", "power on", "brownout")):
        return "POWER_INTERRUPTION", "Uptime-Reset und neuer Power-on-Resetgrund"
    if rebooted:
        return "SOFTWARE_RESTART", "Uptime-Reset zeitgleich mit Neustart"
    if offline_seconds:
        return "NETWORK_OR_API_OUTAGE", "API zeitweise nicht erreichbar; kein Uptime-Reset beobachtet"
    if during.get("isUsingFallbackStratum"):
        return "POOL_OR_STRATUM_ISSUE", "Fallback-Pool aktiv"
    return "UNKNOWN", fault


def duration_text(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return (f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s")


def metrics_text(data):
    return (f"{data.get('hashRate') or 0:.0f} GH/s, {data.get('power') or 0:.1f} W, "
            f"{(data.get('voltage') or 0) / 1000:.2f} V, {data.get('temp') or 0:.1f} °C, "
            f"Uptime {duration_text(data.get('uptimeSeconds') or 0)}")


def health_summary(state, data, last_incident=None):
    parts = []
    if state == "USER PAUSED":
        parts.append("Mining bewusst über AxeOS pausiert")
    elif state == "ONLINE":
        parts.append(f"Mining stabil · {(data.get('hashRate') or 0) / 1000:.2f} TH/s")
    elif state == "MINING STALLED":
        parts.append("Mining gestoppt, Controller weiterhin erreichbar")
    elif state == "OFFLINE":
        parts.append("AxeOS/API derzeit nicht erreichbar")
    else:
        parts.append("Messwerte werden auf Auffälligkeiten geprüft")
    volts = (data.get("voltage") or 0) / 1000
    if volts:
        parts.append(f"Input {volts:.2f} V")
    parts.append(f"ASIC {data.get('temp') or 0:.0f} °C · VR {data.get('vrTemp') or 0:.0f} °C")
    parts.append(f"Reject-Rate {data.get('rejectRate') or 0:.2f} %")
    if last_incident:
        parts.append("letzter Vorfall vor " + duration_text(now() - last_incident))
    return " · ".join(parts)


def market_poller():
    global market_cache
    while True:
        started = time.monotonic()
        try:
            with market_lock:
                address = miner_address
            if not address:
                time.sleep(2)
                continue
            network = get_json(PUBLIC_POOL_API_URL + "/network")
            price = get_json(BTC_PRICE_URL)
            try:
                candles = get_json(BTC_HISTORY_URL)
            except Exception:
                candles = []
            client = get_json(PUBLIC_POOL_API_URL + "/client/" + quote(address, safe=""))
            workers = [{k: w.get(k) for k in ("name", "payoutMode", "bestDifficulty", "hashRate", "startTime", "lastSeen")}
                       for w in (client.get("workers") or [])]
            accounting = client.get("accounting") or {}
            safe_miner = {
                "bestDifficulty": accounting.get("bestSubmissionDifficulty") or client.get("bestDifficulty"),
                "workersCount": client.get("workersCount"),
                "hashRate": sum((w.get("hashRate") or 0) for w in workers),
                "soloWork": accounting.get("workSinceLastBlock"),
                "lastSeen": accounting.get("latestShareAt") or (workers[0].get("lastSeen") if workers else None),
                "workers": workers,
            }
            safe_network = {
                "blocks": network.get("blocks"),
                "difficulty": network.get("difficulty"),
                "networkhashps": network.get("networkhashps"),
            }
            btc_eur = float(price["data"]["amount"])
            price_history, price_change, price_low, price_high = summarize_candles(
                candles, now() - 24 * 3600)
            if not price_history:
                with market_lock:
                    price_history = list(market_cache.get("price_history") or [])
                    price_change = market_cache.get("price_change_pct")
                    price_low = market_cache.get("price_low")
                    price_high = market_cache.get("price_high")
            subsidy = block_subsidy(safe_network["blocks"])
            with market_lock:
                market_cache = {"updated": now(), "btc_eur": btc_eur, "block_btc": subsidy,
                                "price_history": price_history, "price_change_pct": price_change,
                                "price_low": price_low, "price_high": price_high,
                                "miner": safe_miner, "network": safe_network}
        except Exception as exc:
            print("market poll failed:", type(exc).__name__, flush=True)
        time.sleep(max(1, MARKET_SECONDS - (time.monotonic() - started)))


def previous():
    with db() as con:
        row = con.execute("SELECT payload FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def expected_hashrate(data):
    value = EXPECTED_HASHRATE or (data or {}).get("expectedHashrate") or 0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def stable_hashrate_baseline(data, at=None):
    """Median of recent healthy telemetry, with AxeOS expectedHashrate as safe fallback."""
    stamp = int(at or now())
    expected = expected_hashrate(data)
    floor = expected * 0.8 if expected else 0
    try:
        with db() as con:
            rows = con.execute("SELECT hashrate FROM samples WHERE ts>=? AND ts<? AND hashrate>=? ORDER BY ts",
                               (stamp - 3600, stamp, floor)).fetchall()
    except sqlite3.Error:
        rows = []
    recent = [float(row[0]) for row in rows if row[0] is not None]
    if len(recent) >= 12:
        return statistics.median(recent)
    smoothed = [float(data.get(key) or 0) for key in ("hashRate_1h", "hashRate_10m", "expectedHashrate")]
    healthy = [value for value in smoothed if value >= floor and value > 0]
    return statistics.median(healthy) if healthy else expected


def hashrate_degradation(data, baseline=None):
    """Return evidence for sustained partial mining loss, or None when restart is unsafe."""
    data = data or {}
    expected = expected_hashrate(data)
    baseline = float(baseline or stable_hashrate_baseline(data))
    threshold = baseline * (1 - AUTO_RESTART_LOSS)
    rate = float(data.get("hashRate") or 0)
    safe = (
        baseline > 0 and rate < threshold
        and (data.get("uptimeSeconds") or 0) >= AUTO_RESTART_MIN_UPTIME
        and (data.get("power") or 0) > IDLE_POWER_W
        and (data.get("actualFrequency") or 0) > 0
        and not data.get("miningPaused")
        and not data.get("isUsingFallbackStratum")
        and not data.get("overheat_mode")
        and not data.get("power_fault")
        and not data.get("hardware_fault")
    )
    return {"hashrate": rate, "expected": expected, "baseline": baseline,
            "threshold": threshold, "loss_pct": (baseline - rate) * 100 / baseline} if safe else None


def axeos_restart_url(api_url=API_URL):
    parsed = urlparse(api_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/api/system/restart", "", "", ""))


def request_axeos_restart():
    req = urllib.request.Request(
        axeos_restart_url(), data=b"", method="POST",
        headers={"Accept": "application/json", "User-Agent": "BitaxeMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=6) as response:
        return response.status


def axeos_system_url(api_url=API_URL):
    parsed = urlparse(api_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/api/system", "", "", ""))


def update_axeos_profile(profile):
    payload = {key: profile[key] for key in
               ("frequency", "coreVoltage", "temptarget", "autofanspeed", "fanspeed", "overclockEnabled")}
    req = urllib.request.Request(
        axeos_system_url(), data=json.dumps(payload).encode(), method="PATCH",
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "User-Agent": "BitaxeMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=8) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError("AxeOS rejected profile")
        return response.status


def active_mining_profile(data):
    for key, profile in MINING_PROFILES.items():
        if (data.get("frequency") == profile["frequency"]
                and data.get("coreVoltage") == profile["coreVoltage"]
                and data.get("temptarget") == profile["temptarget"]
                and int(data.get("autofanspeed") or 0) == profile["autofanspeed"]
                and int(data.get("fanspeed") or 0) == profile["fanspeed"]
                and int(data.get("overclockEnabled") or 0) == profile["overclockEnabled"]):
            return key
    return None


def state_value(key, default=None):
    with db() as con:
        row = con.execute("SELECT value FROM monitor_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_state_value(key, value):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO monitor_state(key,value) VALUES(?,?)", (key, str(value)))


def user_pause_active():
    return state_value("user_pause_active", "false").lower() == "true"


def user_pause_grace_active(at=None):
    return int(at or now()) < int(state_value("user_pause_grace_until", "0") or 0)


def record_user_pause_transition(old, new, stamp=None):
    """Persist observed AxeOS pause transitions without creating a technical incident."""
    stamp = int(stamp or now())
    paused = bool((new or {}).get("miningPaused"))
    was_paused = user_pause_active()
    if paused and not was_paused:
        add_event("USER_PAUSED", "info", "Mining wurde über AxeOS pausiert.", stamp,
                  details={"observed": {"miningPaused": True}})
        set_state_value("user_pause_active", "true")
        set_state_value("user_pause_started_at", stamp)
        set_state_value("user_pause_grace_until", "0")
        return "paused"
    if not paused and was_paused:
        add_event("USER_RESUMED", "info", "Mining wurde fortgesetzt.", stamp,
                  details={"observed": {"miningPaused": False},
                           "grace_seconds": USER_RESUME_GRACE_SECONDS})
        set_state_value("user_pause_active", "false")
        set_state_value("user_pause_grace_until", stamp + USER_RESUME_GRACE_SECONDS)
        return "resumed"
    return None


def discard_transient_auto_incident(incident_id):
    """Remove an incident created in the same cycle when a final pause guard cancels recovery."""
    if not incident_id:
        return
    with db() as con:
        con.execute("DELETE FROM incident_samples WHERE incident_id=?", (incident_id,))
        con.execute("DELETE FROM incident_diagnostics WHERE incident_id=?", (incident_id,))
        con.execute("DELETE FROM events WHERE incident_id=?", (incident_id,))
        con.execute("DELETE FROM incidents WHERE id=?", (incident_id,))


def offline_event_context():
    planned = user_pause_active()
    return {"planned": planned, "severity": "info" if planned else "critical",
            "message": ("Bitaxe nach Benutzerpause nicht erreichbar" if planned
                        else "Bitaxe seit mehreren Polls nicht erreichbar")}


def auto_restart_enabled():
    return AUTO_RESTART_ENABLED or state_value("auto_restart_enabled", "false").lower() == "true"


def validate_dashboard_layout(layout):
    if not isinstance(layout, list) or len(layout) != len(LAYOUT_WIDGETS):
        raise ValueError("layout must contain every widget exactly once")
    result, seen = [], set()
    for item in layout:
        if not isinstance(item, dict) or item.get("id") not in LAYOUT_WIDGETS or item["id"] in seen:
            raise ValueError("invalid or duplicate widget")
        seen.add(item["id"])
        result.append({"id": item["id"], "collapsed": bool(item.get("collapsed")),
                       "hidden": bool(item.get("hidden"))})
    if seen != set(LAYOUT_WIDGETS):
        raise ValueError("layout is incomplete")
    return result


def validate_layout_name(value):
    name = str(value or "").strip()
    if not name or len(name) > 40 or name.casefold() in {"standard", "standardlayout"}:
        raise ValueError("Bitte einen eigenen Layoutnamen mit maximal 40 Zeichen verwenden")
    return name


def store_dashboard_layout(name, layout, overwrite=False):
    stamp = now()
    encoded = json.dumps(layout, separators=(",", ":"))
    with db() as con:
        if overwrite:
            return bool(con.execute(
                "UPDATE dashboard_layouts SET layout_json=?,updated_at=? WHERE name=?",
                (encoded, stamp, name)).rowcount)
        con.execute("INSERT INTO dashboard_layouts(name,layout_json,created_at,updated_at) VALUES(?,?,?,?)",
                    (name, encoded, stamp, stamp))
    return True


def auto_restart_outcome(attempt, elapsed, recovered):
    if recovered:
        return "recovered"
    if elapsed < AUTO_RESTART_VERIFY_SECONDS:
        return "waiting"
    return "retry" if attempt < AUTO_RESTART_MAX_ATTEMPTS else "lock"


def detect(old, new, stamp=None):
    stamp = int(stamp or now())
    record_user_pause_transition(old or {}, new, stamp)
    if not old:
        add_event("START", "info", "Monitoring gestartet", stamp)
        return
    checks = [
        ("POWER", (old.get("power") or 0) <= POWER_HIGH < (new.get("power") or 0), "warning", f"Leistung über {POWER_HIGH:g} W"),
        ("TEMPERATURE", (old.get("temp") or 0) <= TEMP_HIGH < (new.get("temp") or 0), "critical", f"Temperatur über {TEMP_HIGH:g} °C"),
        ("FALLBACK_POOL", not bool(old.get("isUsingFallbackStratum")) and bool(new.get("isUsingFallbackStratum")), "warning", "Fallback-Pool aktiv"),
        ("BLOCK_CANDIDATE", (new.get("blockFound") or 0) > (old.get("blockFound") or 0), "critical", "BLOCK CANDIDATE DETECTED – Bestätigung durch Pool/Netzwerk prüfen"),
        ("OVERHEAT", not bool(old.get("overheat_mode")) and bool(new.get("overheat_mode")), "critical", "Überhitzungsschutz aktiv"),
    ]
    for kind, fired, severity, message in checks:
        if fired:
            add_event(kind, severity, message)
    old_uptime, new_uptime = old.get("uptimeSeconds"), new.get("uptimeSeconds")
    if (isinstance(old_uptime, (int, float)) and isinstance(new_uptime, (int, float))
            and new_uptime + 30 < old_uptime):
        add_event("REBOOT", "warning", "Neustart erkannt: " + str(new.get("resetReason") or "Uptime-Reset"))
    pending_profile = state_value("pending_mining_profile")
    if pending_profile and active_mining_profile(new) == pending_profile:
        profile = MINING_PROFILES[pending_profile]
        add_event("MINING_PROFILE_ACTIVE", "info",
                  f"Mining-Profil {profile['label']} durch AxeOS-Telemetrie bestätigt",
                  details={"profile": pending_profile, "frequency": profile["frequency"],
                           "coreVoltage": profile["coreVoltage"], "temptarget": profile["temptarget"],
                           "autofanspeed": profile["autofanspeed"], "fanspeed": profile["fanspeed"]})
        set_state_value("pending_mining_profile", "")
    if (new.get("sharesRejected") or 0) > (old.get("sharesRejected") or 0):
        reason = new.get("sharesRejectedReasons")
        suffix = f" · Grund: {reason}" if reason else ""
        add_event("REJECTED_SHARE", "info", f"Rejected Shares: {old.get('sharesRejected') or 0} → {new.get('sharesRejected')}{suffix}")


def save(data, ts):
    values = (ts, data.get("power"), data.get("voltage"), data.get("current"), data.get("temp"),
              data.get("vrTemp"), data.get("coreVoltageActual"), data.get("actualFrequency"),
              data.get("hashRate"), data.get("hashRate_1m"), data.get("hashRate_10m"),
              data.get("hashRate_1h"), data.get("errorPercentage"), data.get("sharesAccepted"),
              data.get("sharesRejected"), data.get("bestDiff"), data.get("bestSessionDiff"),
              data.get("poolDifficulty"), data.get("networkDifficulty"), data.get("responseTime"),
              data.get("uptimeSeconds"), data.get("totalUptimeSeconds"), data.get("fanrpm"),
              data.get("wifiRSSI"), int(bool(data.get("isUsingFallbackStratum"))),
              data.get("blockFound"), json.dumps(data, separators=(",", ":")))
    with db() as con:
        con.execute("INSERT OR REPLACE INTO samples VALUES(" + ",".join("?" * 27) + ")", values)


def poller():
    global miner_address
    failures = successes = stopped_polls = 0
    state = "ONLINE"
    incident_id = None
    incident_start = None
    incident_before = None
    incident_during = None
    offline_since = None
    degraded_since = None
    degradation_baseline = None
    degradation_values = []
    domain_stall_since = None
    domain_stall_polls = 0
    auto_kind = None
    auto_mode = False
    auto_restart_stamp = 0
    auto_recovery_polls = 0
    auto_attempt = 0
    auto_uptime_reset = False
    auto_locked = state_value("auto_restart_locked", "false").lower() == "true"
    normal_polls = 0
    last_auto_restart = int(state_value("last_auto_restart", "0") or 0)
    with db() as con:
        active = con.execute("SELECT id,started_at,before_sample,kind,facts FROM incidents WHERE status='ACTIVE' ORDER BY id DESC LIMIT 1").fetchone()
    if active:
        incident_id, incident_start = active["id"], active["started_at"]
        incident_before = json.loads(active["before_sample"]) if active["before_sample"] else previous()
        facts = json.loads(active["facts"] or "{}")
        auto_mode = active["kind"] in {"HASHRATE_DEGRADATION", "ASIC_DOMAIN_STALL"} and bool(facts.get("auto_restart"))
        auto_kind = active["kind"] if auto_mode else None
        auto_restart_stamp = int(facts.get("restart_requested_at") or 0)
        auto_attempt = int(facts.get("attempt") or 1)
        auto_uptime_reset = bool(facts.get("uptime_reset"))
    while True:
        started = time.monotonic()
        try:
            raw = fetch_bitaxe_info()
            address = mining_address(raw)
            if address:
                with market_lock:
                    miner_address = address
            data = clean(raw)
            old = previous()
            stamp = now()
            was_user_paused = user_pause_active()
            previous_failures = failures
            detect(old, data, stamp)
            save(data, stamp)
            failures = 0
            successes += 1
            pause_now = bool(data.get("miningPaused"))
            pause_guard = pause_now or user_pause_grace_active(stamp)
            if previous_failures >= OFFLINE_AFTER_POLLS and was_user_paused:
                add_event("RECOVERED", "info", "Bitaxe nach Benutzerpause wieder erreichbar", stamp,
                          details={"after_user_pause": True})
            hashrate = data.get("hashRate") or 0
            fact = observed_cause(data)
            rebooted = bool(old and (data.get("uptimeSeconds") or 0) + 30 < (old.get("uptimeSeconds") or 0))
            stopped = not pause_guard and ((hashrate <= 10) or bool(fact))
            if pause_guard:
                stopped_polls = 0
                degraded_since = None
                degradation_baseline = None
                degradation_values = []
                domain_stall_since = None
                domain_stall_polls = 0
            if pause_now and auto_mode:
                add_event("AUTO_RECOVERY_CANCELLED_USER_PAUSED", "info",
                          "Weitere Auto-Recovery abgebrochen: Mining wurde über AxeOS pausiert",
                          stamp, automatic=True, details={"reason": "miningPaused=true"})
                update_incident(incident_id, ended_at=stamp, status="RESOLVED", severity="warning",
                                summary="Auto-Recovery durch bewusste Benutzerpause beendet",
                                recovery="Mining wurde über AxeOS pausiert")
                incident_id = incident_start = incident_before = incident_during = offline_since = None
                auto_mode = False
                auto_kind = None
                auto_recovery_polls = 0
            if degraded_since is None and not auto_mode:
                degradation_baseline = stable_hashrate_baseline(data, stamp)
            degradation = None if pause_guard else hashrate_degradation(data, degradation_baseline)
            observed_domain_stall = None if pause_guard else domain_stall_evidence(data)
            if observed_domain_stall and not auto_mode:
                domain_stall_since = domain_stall_since or stamp
                domain_stall_polls += 1
                if domain_stall_polls == DOMAIN_STALL_POLLS:
                    add_event("ASIC_DOMAIN_STALL_DETECTED", "critical",
                              f"ASIC-Domain-Stall erkannt: {len(observed_domain_stall['stalled_indexes'])} von "
                              f"{len(observed_domain_stall['domains'])} Domains ohne Hashrate",
                              details=observed_domain_stall)
            elif not auto_mode:
                domain_stall_since = None
                domain_stall_polls = 0
            confirmed_domain_stall = (observed_domain_stall if domain_stall_polls >= DOMAIN_STALL_POLLS else None)
            expected = degradation_baseline or expected_hashrate(data)
            recovery_threshold = expected * 0.80 if expected else 0
            healthy_hashrate = expected > 0 and hashrate >= recovery_threshold
            normal_polls = normal_polls + 1 if healthy_hashrate and not fact and not pause_guard else 0
            if auto_locked and normal_polls >= RECOVERY_POLLS:
                auto_locked = False
                set_state_value("auto_restart_locked", "false")
                add_event("AUTO_RESTART_REARMED", "info",
                          "Automatischer Neustart nach stabiler Hashrate wieder freigegeben")
            if degradation and not auto_mode:
                if degraded_since is None:
                    degraded_since = stamp
                    degradation_values = []
                    add_event("HASHRATE_DROP_DETECTED", "warning",
                              f"Hashrate-Einbruch erkannt: {hashrate:.0f} GH/s bei Basis {expected:.0f} GH/s",
                              details={"baseline_gh": expected, "threshold_gh": degradation["threshold"],
                                       "observed_gh": hashrate, "loss_pct": degradation["loss_pct"]})
                degradation_values.append(hashrate)
            elif not auto_mode:
                degraded_since = None
                degradation_baseline = None
                degradation_values = []

            trigger_evidence = confirmed_domain_stall or degradation
            trigger_since = domain_stall_since if confirmed_domain_stall else degraded_since
            trigger_after = DOMAIN_STALL_AFTER_SECONDS if confirmed_domain_stall else AUTO_RESTART_AFTER_SECONDS
            cooldown_ready = bool(confirmed_domain_stall) or stamp - last_auto_restart >= AUTO_RESTART_COOLDOWN
            rolling_limit_ready = automatic_restarts_since(stamp - 3600) < AUTO_RESTART_MAX_ATTEMPTS
            if (auto_restart_enabled() and trigger_evidence and trigger_since
                    and stamp - trigger_since >= trigger_after
                    and not rolling_limit_ready and not auto_locked and not pause_guard):
                auto_locked = True
                set_state_value("auto_restart_locked", "true")
                add_event("AUTO_RECOVERY_SUPPRESSED", "critical",
                          "Automatischer Neustart unterdrückt: bereits zwei Versuche in 60 Minuten",
                          details={"window_seconds": 3600, "max_attempts": AUTO_RESTART_MAX_ATTEMPTS})
            if (auto_restart_enabled() and not auto_locked and not auto_mode
                    and incident_id is None and trigger_evidence
                    and trigger_since and stamp - trigger_since >= trigger_after
                    and cooldown_ready and rolling_limit_ready and not pause_guard):
                incident_before = old
                incident_start = trigger_since
                auto_restart_stamp = stamp
                auto_attempt = 1
                auto_uptime_reset = False
                auto_kind = "ASIC_DOMAIN_STALL" if confirmed_domain_stall else "HASHRATE_DEGRADATION"
                facts = {"auto_restart": True, "restart_requested_at": stamp, "attempt": auto_attempt,
                         "trigger": auto_kind, "baseline_gh": expected, "observed_gh": hashrate,
                         "duration_seconds": stamp - trigger_since,
                         "domains": (confirmed_domain_stall or {}).get("domains"),
                         "stalled_domain_indexes": (confirmed_domain_stall or {}).get("stalled_indexes")}
                facts["forensics"] = incident_metrics(incident_start, data)
                if degradation:
                    facts.update({"loss_threshold_pct": AUTO_RESTART_LOSS * 100,
                                  "threshold_gh": degradation["threshold"],
                                  "lowest_gh": min(degradation_values or [hashrate]),
                                  "average_gh": sum(degradation_values or [hashrate]) / len(degradation_values or [hashrate])})
                summary = (f"Mindestens zwei ASIC-Domains seit {trigger_after} Sekunden ohne Hashrate; automatischer Neustart angefordert"
                           if confirmed_domain_stall else
                           f"Hashrate seit {AUTO_RESTART_AFTER_SECONDS // 60} Minuten unter {AUTO_RESTART_LOSS * 100:.0f} % Verlust; automatischer Neustart angefordert")
                incident_id = create_incident(
                    incident_start, auto_kind, auto_kind.replace("_", " "), summary,
                    "warning", facts=facts, before_override=incident_before)
                attach_incident_sample(incident_id, stamp, "restart", data)
                set_state_value("last_auto_restart", stamp)
                last_auto_restart = stamp
                auto_mode = True
                auto_recovery_polls = 0
                reason = (f"ASIC-Domain-Stall: Domains {confirmed_domain_stall['stalled_indexes']} ohne Hashrate; AxeOS-Neustart angefordert"
                          if confirmed_domain_stall else
                          f"Hashrate {hashrate:.0f} GH/s unter {degradation['threshold']:.0f} GH/s; AxeOS-Neustart angefordert")
                details = {"reason": reason,
                           "attempt": auto_attempt, **facts}
                try:
                    _, _, request_status = restart_with_persisted_evidence(incident_id, data, details, stamp)
                    if request_status == "cancelled_user_paused":
                        discard_transient_auto_incident(incident_id)
                        incident_id = incident_start = incident_before = incident_during = offline_since = None
                        auto_mode = False
                        auto_kind = None
                        degraded_since = None
                        domain_stall_since = None
                        domain_stall_polls = 0
                except Exception as restart_error:
                    add_event("AUTO_RECOVERY_SUPPRESSED", "critical",
                              "Neustart nicht ausgeführt: Diagnose konnte nicht sicher gespeichert werden",
                              incident_id=incident_id, automatic=True,
                              details={"error_type": type(restart_error).__name__})

            if auto_mode:
                attach_incident_sample(incident_id, stamp, "after_restart", data)
                expected = degradation_baseline or expected_hashrate(data)
                recovered = (expected > 0 and hashrate >= expected * 0.80
                             and (auto_kind != "ASIC_DOMAIN_STALL" or domains_recovered(data)))
                auto_recovery_polls = auto_recovery_polls + 1 if recovered else 0
                auto_uptime_reset = auto_uptime_reset or rebooted
                stored_forensics = persisted_incident_forensics(incident_id)
                facts = {"auto_restart": True, "restart_requested_at": auto_restart_stamp,
                         "attempt": auto_attempt, "uptime_reset": auto_uptime_reset, "trigger": auto_kind,
                         "observed_gh": hashrate,
                         "domains": ((stored_forensics or {}).get("domains")
                                     or list(domain_register_paths(data).values())),
                         "baseline_gh": expected, "loss_threshold_pct": AUTO_RESTART_LOSS * 100,
                         "recovery_threshold_gh": expected * 0.80 if expected else None,
                         "forensics": stored_forensics}
                update_incident(incident_id, facts=facts,
                                summary="Automatischer AxeOS-Neustart ausgelöst; Wiederherstellung wird geprüft")
                outcome = auto_restart_outcome(
                    auto_attempt, stamp - auto_restart_stamp,
                    auto_recovery_polls >= RECOVERY_POLLS)
                if outcome == "recovered":
                    recovery = f"Hashrate nach automatischem Neustart wieder stabil ({hashrate:.0f} GH/s)"
                    update_incident(incident_id, ended_at=stamp, status="RESOLVED",
                                    summary=("ASIC-Domain-Stall automatisch durch AxeOS-Neustart behoben"
                                             if auto_kind == "ASIC_DOMAIN_STALL" else
                                             "Hashrate-Degradation automatisch durch AxeOS-Neustart behoben"),
                                    recovery=recovery,
                                    after_sample=safe_payload(data), severity="warning", facts=facts)
                    add_event("RECOVERED", "info", recovery, incident_id=incident_id,
                              automatic=True, details=facts)
                    incident_id = incident_start = incident_before = incident_during = offline_since = None
                    auto_mode = False
                    degraded_since = None
                    domain_stall_since = None
                    domain_stall_polls = 0
                    auto_kind = None
                    set_state_value("auto_restart_locked", "false")
                elif outcome == "retry":
                    auto_attempt += 1
                    auto_restart_stamp = stamp
                    last_auto_restart = stamp
                    auto_recovery_polls = 0
                    facts.update({"attempt": auto_attempt, "restart_requested_at": stamp})
                    set_state_value("last_auto_restart", stamp)
                    update_incident(incident_id, severity="warning", facts=facts,
                                    summary="Erster Neustart ohne ausreichende Erholung; zweiter Versuch angefordert")
                    attach_incident_sample(incident_id, stamp, "retry", data)
                    details = {"reason": "Erster Neustart erfolglos; zweiter und letzter Versuch angefordert",
                               "attempt": auto_attempt, **facts}
                    try:
                        if automatic_restarts_since(stamp - 3600) >= AUTO_RESTART_MAX_ATTEMPTS:
                            raise RuntimeError("rolling_restart_limit")
                        _, _, request_status = restart_with_persisted_evidence(incident_id, data, details, stamp)
                        if request_status == "cancelled_user_paused":
                            update_incident(incident_id, ended_at=stamp, status="RESOLVED", severity="warning",
                                            summary="Zweiter Neustart durch bewusste Benutzerpause abgebrochen",
                                            recovery="Mining wurde über AxeOS pausiert")
                            auto_mode = False
                            auto_kind = None
                    except Exception as restart_error:
                        add_event("AUTO_RECOVERY_SUPPRESSED", "critical",
                                  "Zweiter Neustart nicht ausgeführt: Diagnose konnte nicht sicher gespeichert werden",
                                  incident_id=incident_id, automatic=True,
                                  details={"error_type": type(restart_error).__name__})
                elif outcome == "lock":
                    message = "Hashrate nach zwei automatischen Neustarts nicht erholt; Automatik bis zur nächsten stabilen Erholung gesperrt"
                    update_incident(incident_id, ended_at=stamp, status="RESOLVED", severity="critical",
                                    summary=message, recovery=message, after_sample=safe_payload(data), facts=facts)
                    add_event("AUTO_RESTART_FAILED", "critical", message)
                    incident_id = incident_start = incident_before = incident_during = offline_since = None
                    auto_mode = False
                    auto_locked = True
                    auto_kind = None
                    set_state_value("auto_restart_locked", "true")
                    degraded_since = stamp
                state = "RECOVERING" if auto_mode else ("ONLINE" if recovered else "DEGRADED")
                with db() as con:
                    con.execute("INSERT OR REPLACE INTO monitor_state(key,value) VALUES('health_state',?)", (state,))
                time.sleep(max(1, POLL_SECONDS - (time.monotonic() - started)))
                continue
            if stopped:
                if stopped_polls == 0:
                    incident_before = old
                stopped_polls += 1
                incident_during = data
            else:
                stopped_polls = 0
            if incident_id is None and (fact or stopped_polls >= STALL_AFTER_POLLS):
                incident_start = stamp if fact else stamp - POLL_SECONDS * (STALL_AFTER_POLLS - 1)
                kind, reason = classify_incident(incident_before, data)
                incident_id = create_incident(incident_start, kind, kind.replace("_", " "),
                                              reason or "Ursache nicht eindeutig", "critical" if fact else "warning",
                                              fact, {"controller_reachable": True, "uptime_reset": False},
                                              incident_before)
                successes = 0
            if incident_id:
                attach_incident_sample(incident_id, stamp, "during", data)
                offline_duration = 0 if offline_since is None else stamp - offline_since
                kind, reason = classify_incident(incident_before, incident_during or data, data if rebooted else {}, offline_duration)
                facts = {"controller_reachable": True, "uptime_reset": rebooted,
                         "offline_seconds": offline_duration, "reset_reason": data.get("resetReason") if rebooted else None}
                update_incident(incident_id, kind=kind, title=kind.replace("_", " "),
                                summary=reason or "Ursache nicht eindeutig", observed_cause=fact, facts=facts)
                if not stopped and successes >= RECOVERY_POLLS:
                    recovery = ("Mining nach Neustart stabil" if rebooted else "Mining wieder stabil")
                    update_incident(incident_id, ended_at=stamp, status="RESOLVED", recovery=recovery,
                                    after_sample=safe_payload(data), severity="warning")
                    add_event("RECOVERED", "info", recovery)
                    incident_id = incident_start = incident_before = incident_during = offline_since = None
            if pause_now:
                state = "USER PAUSED"
            elif state in {"OFFLINE", "RECOVERING", "USER PAUSED"}:
                state = "RECOVERING" if successes < RECOVERY_POLLS else "ONLINE"
            else:
                state = "DEGRADED" if stopped_polls else "ONLINE"
        except Exception as exc:
            failures += 1
            successes = 0
            state = "DEGRADED" if failures < OFFLINE_AFTER_POLLS else "OFFLINE"
            if failures == OFFLINE_AFTER_POLLS and not auto_mode:
                offline_since = now() - POLL_SECONDS * (OFFLINE_AFTER_POLLS - 1)
                offline_context = offline_event_context()
                planned_pause = offline_context["planned"]
                if incident_id is None and not planned_pause:
                    incident_start = offline_since
                    incident_before = previous()
                    incident_id = create_incident(incident_start, "UNKNOWN", "GERÄT NICHT ERREICHBAR",
                                                  "API seit mehreren Polls nicht erreichbar", "critical",
                                                  facts={"controller_reachable": False}, before_override=incident_before)
                add_event("OFFLINE", offline_context["severity"], offline_context["message"],
                          details={"after_user_pause": planned_pause})
            if incident_id and not auto_mode:
                update_incident(incident_id, summary="API nicht erreichbar; Ursache noch nicht eindeutig",
                                facts={"controller_reachable": False, "failed_polls": failures})
            print("poll failed:", type(exc).__name__, flush=True)
        with db() as con:
            con.execute("INSERT OR REPLACE INTO monitor_state(key,value) VALUES('health_state',?)", (state,))
        time.sleep(max(1, POLL_SECONDS - (time.monotonic() - started)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("http", self.address_string(), fmt % args, flush=True)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if p.path == "/healthz":
            with db() as con:
                row = con.execute("SELECT MAX(ts) FROM samples").fetchone()
            age = None if not row[0] else now() - row[0]
            return self.send_json({"status": "ok" if age is not None and age < POLL_SECONDS * 3 else "degraded", "last_sample_age": age}, 200 if age is not None and age < POLL_SECONDS * 3 else 503)
        if p.path == "/api/settings/auto-restart":
            return self.send_json({"enabled": auto_restart_enabled(),
                                   "loss_threshold_pct": AUTO_RESTART_LOSS * 100,
                                   "after_seconds": AUTO_RESTART_AFTER_SECONDS,
                                   "domain_stall_polls": DOMAIN_STALL_POLLS,
                                   "domain_stall_after_seconds": DOMAIN_STALL_AFTER_SECONDS,
                                   "cooldown_seconds": AUTO_RESTART_COOLDOWN,
                                   "max_attempts": AUTO_RESTART_MAX_ATTEMPTS,
                                   "locked": state_value("auto_restart_locked", "false").lower() == "true"})
        if p.path == "/api/layouts":
            with db() as con:
                rows = con.execute("SELECT name,layout_json,created_at,updated_at "
                                   "FROM dashboard_layouts ORDER BY name COLLATE NOCASE").fetchall()
            return self.send_json([{"name": row["name"], "layout": json.loads(row["layout_json"]),
                                    "created_at": row["created_at"], "updated_at": row["updated_at"]}
                                   for row in rows])
        if p.path == "/api/settings/mining-profile":
            with db() as con:
                row = con.execute("SELECT payload FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
            data = safe_payload(json.loads(row["payload"])) if row else {}
            return self.send_json({"active": active_mining_profile(data), "current": {
                "frequency": data.get("frequency"), "coreVoltage": data.get("coreVoltage"),
                "temptarget": data.get("temptarget"), "autofanspeed": data.get("autofanspeed"),
                "fanspeed": data.get("fanspeed"),
                "overclockEnabled": data.get("overclockEnabled")},
                "profiles": MINING_PROFILES})
        if p.path == "/api/current":
            with db() as con:
                row = con.execute("SELECT ts,payload FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
                state_row = con.execute("SELECT value FROM monitor_state WHERE key='health_state'").fetchone()
                last_incident = con.execute("SELECT started_at FROM incidents ORDER BY started_at DESC LIMIT 1").fetchone()
            if not row:
                return self.send_json({"online": False, "age_seconds": None, "data": {}})
            age = now() - row["ts"]
            data = safe_payload(json.loads(row["payload"]))
            accepted, rejected = data.get("sharesAccepted") or 0, data.get("sharesRejected") or 0
            data["rejectRate"] = rejected * 100 / max(1, accepted + rejected)
            expected = data.get("expectedHashrate") or EXPECTED_HASHRATE or None
            data["expectedHashrate"] = expected
            state = state_row[0] if state_row else ("ONLINE" if age < POLL_SECONDS * 3 else "OFFLINE")
            if state == "DEGRADED" and (data.get("hashRate") or 0) <= 10:
                state = "MINING STALLED"
            summary = health_summary(state, data, last_incident[0] if last_incident else None)
            if auto_restart_enabled():
                summary += " · Auto-Restart aktiv"
            return self.send_json({"online": age < POLL_SECONDS * 3, "state": state,
                                    "age_seconds": age, "summary": summary, "data": data,
                                    "hashrate_history": cached_historical_hashrate(),
                                    "voltage_24h": voltage_summary(),
                                    "auto_restart": {"enabled": auto_restart_enabled(),
                                        "loss_threshold_pct": AUTO_RESTART_LOSS * 100,
                                        "after_seconds": AUTO_RESTART_AFTER_SECONDS,
                                        "domain_stall_polls": DOMAIN_STALL_POLLS,
                                        "domain_stall_after_seconds": DOMAIN_STALL_AFTER_SECONDS,
                                        "cooldown_seconds": AUTO_RESTART_COOLDOWN,
                                        "max_attempts": AUTO_RESTART_MAX_ATTEMPTS,
                                        "locked": state_value("auto_restart_locked", "false").lower() == "true",
                                        "last_attempt": int(state_value("last_auto_restart", "0") or 0)}})
        if p.path == "/api/events":
            with db() as con:
                rows = con.execute("SELECT ts,kind,severity,message FROM events WHERE kind <> 'HASHRATE' ORDER BY ts DESC LIMIT 100").fetchall()
            return self.send_json([dict(r) for r in rows])
        if p.path == "/api/incidents":
            with db() as con:
                rows = con.execute("""SELECT * FROM incidents WHERE kind <> 'USER_PAUSED'
                    ORDER BY started_at DESC LIMIT 100""").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                for key in ("facts", "before_sample", "after_sample", "pre_stats"):
                    item[key] = json.loads(item[key]) if item.get(key) else None
                result.append(item)
            return self.send_json(result)
        if p.path.startswith("/api/incidents/"):
            try:
                incident_id = int(p.path.rsplit("/", 1)[1])
            except ValueError:
                return self.send_json({"error": "invalid incident"}, 400)
            with db() as con:
                row = con.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
                if row:
                    window = incident_window(row["started_at"], row["ended_at"])
                    window_start, window_end = window["start"], window["end"]
                    telemetry = con.execute("SELECT ts,payload FROM samples WHERE ts BETWEEN ? AND ? ORDER BY ts",
                                            (window_start, window_end)).fetchall()
                    diagnostics = con.execute("SELECT * FROM incident_diagnostics WHERE incident_id=? ORDER BY captured_at",
                                              (incident_id,)).fetchall()
                    related_events = con.execute("""SELECT ts,kind,severity,message,automatic,details FROM events
                        WHERE incident_id=? OR ts BETWEEN ? AND ? ORDER BY ts""",
                        (incident_id, window_start, window_end)).fetchall()
            if not row:
                return self.send_json({"error": "not found"}, 404)
            item = dict(row)
            for key in ("facts", "before_sample", "after_sample", "pre_stats"):
                item[key] = json.loads(item[key]) if item.get(key) else None
            item["samples"] = [{"ts": s["ts"], **safe_payload(json.loads(s["payload"]))} for s in telemetry]
            item["diagnostics"] = [{"captured_at": d["captured_at"], "source_status": d["source_status"],
                "observed": json.loads(d["observed_json"]), "derived": json.loads(d["derived_json"]),
                "field_info": json.loads(d["raw_field_names_json"])} for d in diagnostics]
            snapshot = ((item.get("diagnostics") or [{}])[-1].get("observed")
                        if item.get("diagnostics") else None)
            item["forensics"] = (item.get("facts") or {}).get("forensics") or incident_metrics(
                item["started_at"], snapshot or item.get("before_sample"))
            item["window"] = {"start": window_start, "end": window_end}
            item["events"] = [{**dict(e), "details": json.loads(e["details"] or "{}")} for e in related_events]
            return self.send_json(item)
        if p.path == "/api/market":
            with market_lock:
                payload = dict(market_cache)
                payload["miner"] = dict(market_cache["miner"])
                payload["miner"]["workers"] = [dict(w) for w in market_cache["miner"].get("workers", [])]
                payload["network"] = dict(market_cache["network"])
            with db() as con:
                latest = con.execute("SELECT payload FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
            axeos = json.loads(latest["payload"]) if latest else {}
            height = axeos.get("blockHeight") or payload["network"].get("blocks")
            payload["block_value"] = coinbase_block_value(axeos, height, payload["btc_eur"])
            return self.send_json(payload)
        if p.path == "/api/history":
            ranges = {"1h": 3600, "24h": 86400, "7d": 604800}
            seconds = ranges.get(parse_qs(p.query).get("range", ["1h"])[0], 3600)
            bucket = max(10, seconds // 600)
            end = now()
            start = end - seconds
            return self.send_json({"start": start, "end": end, "samples": chart_history(start, end, bucket),
                                   "markers": chart_markers(start, end)})
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        pause_match = re.fullmatch(r"/api/incidents/(\d+)/confirm-user-pause", path)
        if path not in {"/api/settings/auto-restart", "/api/settings/mining-profile", "/api/layouts"} and not pause_match:
            return self.send_error(404)
        try:
            length = min(65536, int(self.headers.get("Content-Length", "0")))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "invalid JSON"}, 400)
        if pause_match:
            if payload.get("confirmed") is not True:
                return self.send_json({"error": "Historische Benutzerpause muss ausdrücklich bestätigt werden"}, 400)
            result = confirm_historical_user_pause(int(pause_match.group(1)))
            if not result:
                return self.send_json({"error": "Vorfall nicht gefunden"}, 404)
            return self.send_json(result, 200)
        if path == "/api/settings/mining-profile":
            key = str(payload.get("profile") or "").lower()
            profile = MINING_PROFILES.get(key)
            if not profile:
                return self.send_json({"error": "Unbekanntes Profil"}, 400)
            if payload.get("confirmed") is not True:
                return self.send_json({"error": "Profilwechsel muss ausdrücklich bestätigt werden"}, 400)
            try:
                current = fetch_bitaxe_info()
                if str(current.get("ASICModel")) != "BM1370" or str(current.get("boardVersion")) != "601":
                    return self.send_json({"error": "Profile sind nur für Gamma 601 / BM1370 freigegeben"}, 409)
                if current.get("power_fault") or current.get("hardware_fault") or current.get("overheat_mode"):
                    return self.send_json({"error": "Profilwechsel wegen aktivem Hardware-, Power- oder Temperaturfehler gesperrt"}, 409)
                details = {"profile": key, "label": profile["label"], "frequency": profile["frequency"],
                           "coreVoltage": profile["coreVoltage"], "temptarget": profile["temptarget"],
                           "autofanspeed": profile["autofanspeed"], "fanspeed": profile["fanspeed"],
                           "custom_oc": profile["custom"]}
                add_event("MINING_PROFILE_REQUESTED", "warning" if profile["custom"] else "info",
                          f"Mining-Profil {profile['label']} angefordert", automatic=False,
                          details=details)
                update_axeos_profile(profile)
                add_event("MINING_PROFILE_SAVED", "info",
                          f"Mining-Profil {profile['label']} vollständig in AxeOS gespeichert", details=details)
                set_state_value("pending_mining_profile", key)
                return self.send_json({"status": "applied", "profile": key, "settings": details}, 200)
            except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                add_event("MINING_PROFILE_FAILED", "critical", "Mining-Profil konnte nicht vollständig aktiviert werden",
                          details={"profile": key, "error_type": type(error).__name__})
                return self.send_json({"error": "AxeOS konnte das Profil nicht vollständig übernehmen"}, 502)
        if path == "/api/layouts":
            try:
                name = validate_layout_name(payload.get("name"))
                layout = validate_dashboard_layout(payload.get("layout"))
                if not store_dashboard_layout(name, layout, payload.get("overwrite") is True):
                    return self.send_json({"error": "Dieses Layout existiert nicht"}, 404)
            except sqlite3.IntegrityError:
                return self.send_json({"error": "Dieser Layoutname existiert bereits"}, 409)
            except ValueError as error:
                return self.send_json({"error": str(error)}, 400)
            return self.send_json({"name": name, "layout": layout}, 201)
        if not isinstance(payload.get("enabled"), bool):
            return self.send_json({"error": "enabled must be boolean"}, 400)
        set_state_value("auto_restart_enabled", str(payload["enabled"]).lower())
        add_event("AUTO_RESTART_SETTING", "info",
                  "Automatischer Hashrate-Neustart " + ("aktiviert" if payload["enabled"] else "deaktiviert"))
        return self.send_json({"enabled": auto_restart_enabled()})


if __name__ == "__main__":
    init_db()
    backfill_historical_incidents()
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=market_poller, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
