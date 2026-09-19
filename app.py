import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse


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
STALL_AFTER_POLLS = max(2, int(os.getenv("STALL_AFTER_POLLS", "3")))
IDLE_POWER_W = float(os.getenv("IDLE_POWER_W", "8"))
VOLTAGE_LOW_V = float(os.getenv("VOLTAGE_LOW_V", "4.75"))
PUBLIC_POOL_API_URL = os.getenv("PUBLIC_POOL_API_URL", "https://public-pool.io:40557/api").rstrip("/")
BTC_PRICE_URL = os.getenv("BTC_PRICE_URL", "https://api.coinbase.com/v2/prices/BTC-EUR/spot")
BTC_HISTORY_URL = os.getenv("BTC_HISTORY_URL", "https://api.exchange.coinbase.com/products/BTC-EUR/candles?granularity=3600")
MARKET_SECONDS = max(60, int(os.getenv("MARKET_SECONDS", "300")))

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
    "coinbaseValueTotalSatoshis", "coinbaseValueUserSatoshis"
)

INCIDENT_KINDS = {
    "POWER_INTERRUPTION", "MINING_STALL", "SOFTWARE_RESTART",
    "NETWORK_OR_API_OUTAGE", "THERMAL_EVENT", "POOL_OR_STRATUM_ISSUE", "UNKNOWN"
}

HTML = r'''<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bitaxe Monitor</title>
<style>
:root{color-scheme:dark;--bg:#070a0f;--card:#101620;--muted:#8390a3;--text:#f3f6fb;--green:#40e0a0;--yellow:#ffc857;--red:#ff5964;--blue:#57a6ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 70% -20%,#172638,#070a0f 45%);color:var(--text);font:15px system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:1600px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;gap:20px;align-items:center}.brand{font-size:clamp(23px,3vw,40px);font-weight:800;letter-spacing:.03em}.status{display:flex;gap:9px;align-items:center;color:var(--muted)}.dot{width:11px;height:11px;border-radius:50%;background:var(--red);box-shadow:0 0 18px currentColor}.dot.ok{background:var(--green)}.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:20px 0}.card{background:linear-gradient(145deg,#121a25,#0d121a);border:1px solid #202b3a;border-radius:16px;padding:16px;min-width:0;box-shadow:0 10px 35px #0005}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.1em}.value{font-size:clamp(22px,2.4vw,38px);font-weight:750;margin-top:7px;white-space:nowrap}.sub{color:var(--muted);margin-top:4px;overflow:hidden;text-overflow:ellipsis}.hashstats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px;margin-top:9px}.hashstat{min-width:0;color:var(--muted);font-size:10px;text-transform:uppercase}.hashstat b{display:block;color:#dce5f1;font-size:13px;line-height:1.2;white-space:nowrap}.hashstat small{display:block;color:var(--muted);font-size:9px;white-space:nowrap}.wide{grid-column:span 3}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}.facts.poolfacts{grid-template-columns:repeat(4,1fr)}.fact{background:#0b1119;border-radius:10px;padding:10px}.fact b{display:block;font-size:18px;margin-top:3px}.healthdetails{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:12px;padding-top:11px;border-top:1px solid #202b3a;color:#bac5d4;font-size:13px}.healthdetails b{color:var(--text);font-weight:650}.pricechart{height:105px;margin:9px 0 2px}.pricechart canvas{width:100%;height:105px}.pricechange{font-weight:700}.pricechange.up{color:var(--green)}.pricechange.down{color:var(--red)}.chart{height:230px;position:relative}.chart canvas{width:100%;height:190px}.dual{height:230px;display:grid;grid-template-rows:1fr 1fr;gap:8px;margin-top:4px}.mini{min-height:0;position:relative}.mini canvas{width:100%;height:94px}.legend{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;color:var(--muted);font-size:12px}.key{display:inline-block;width:18px;height:3px;border-radius:2px;margin:0 6px 3px 0;vertical-align:middle}.key.asic{background:var(--red)}.key.vr{height:0;border-top:3px dashed var(--yellow)}.key.power{background:var(--blue)}.key.voltage{background:var(--green)}.tabs{display:flex;gap:7px}.tabs button{background:#182231;color:#bac5d4;border:0;border-radius:8px;padding:6px 12px;cursor:pointer}.tabs button.active{background:var(--blue);color:#04101d}.events{max-height:310px;overflow:auto}.event{display:grid;grid-template-columns:145px minmax(155px,190px) minmax(0,1fr);gap:12px;padding:10px 0;border-bottom:1px solid #202b3a;align-items:start}.event>*{min-width:0}.event>b{white-space:nowrap}.event>span:last-child{overflow-wrap:anywhere;line-height:1.45}.sev-warning{color:var(--yellow)}.sev-critical{color:var(--red)}.sev-info{color:var(--green)}@media(max-width:1050px){.grid{grid-template-columns:repeat(3,1fr)}.wide{grid-column:span 3}.facts.poolfacts{grid-template-columns:repeat(2,1fr)}}@media(max-width:620px){.wrap{padding:13px}.grid{grid-template-columns:1fr 1fr}.wide{grid-column:span 2}.facts,.facts.poolfacts{grid-template-columns:1fr}.hashstats{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:8px}.event{grid-template-columns:1fr;gap:4px}.event>b{white-space:normal}.top{align-items:flex-start;flex-direction:column}.chart{height:210px}.dual{height:220px}}
.status{flex-wrap:wrap;justify-content:flex-end}.refresh{font-variant-numeric:tabular-nums;white-space:nowrap}
</style></head><body><main class="wrap"><div class="top"><div><div class="brand">₿ BITAXE GAMMA 601</div><div class="sub" id="ver">AxeOS</div></div><div class="status"><span class="dot" id="dot"></span><b id="state">WARTE AUF DATEN</b><span class="refresh" id="seen">Refresh —</span></div></div>
<section class="grid"><div class="card"><div class="label">Hashrate</div><div class="value" id="hash">—</div><div class="hashstats"><div class="hashstat">10m<b id="hash10m">—</b></div><div class="hashstat">1h<b id="hash1h">—</b></div><div class="hashstat">24h<b id="hash24h">—</b><small id="cover24h"></small></div><div class="hashstat">7d<b id="hash7d">—</b><small id="cover7d"></small></div></div></div><div class="card"><div class="label">Leistung</div><div class="value" id="power">—</div><div class="sub" id="voltage">—</div></div><div class="card"><div class="label">ASIC / VR</div><div class="value" id="temp">—</div><div class="sub" id="vr">—</div></div><div class="card"><div class="label">Shares</div><div class="value" id="shares">—</div><div class="sub" id="best">—</div></div><div class="card"><div class="label">Pool / Fehler</div><div class="value" id="pool">—</div><div class="sub" id="errors">—</div></div><div class="card"><div class="label">Laufzeit</div><div class="value" id="uptime">—</div><div class="sub" id="wifi">—</div></div>
<div class="card" style="grid-column:1/-1"><div class="label">Health & Gerätestatus</div><div class="value" id="health" style="font-size:20px">—</div><div class="sub" id="healthText">—</div><div class="healthdetails" id="healthDetails"></div></div>
<div class="card wide"><div class="label">Bitcoin & Blockwert</div><div class="value" id="btcEur">—</div><div class="sub"><span id="priceUpdated">Aktueller BTC/EUR-Kurs</span> · <span class="pricechange" id="priceChange">24h —</span></div><div class="pricechart"><canvas id="btcPriceChart"></canvas></div><div class="facts"><div class="fact" title="Neu erzeugte Bitcoin pro Block gemäß aktuellem Halving-Zyklus."><span class="sub">Block-Subvention</span><b id="blockSubsidy">— BTC</b></div><div class="fact" title="Gebühren der Transaktionen im aktuellen Blocktemplate. Sie kommen zusätzlich zur Block-Subvention hinzu."><span class="sub">Transaktionsgebühren</span><b id="blockFees">—</b></div><div class="fact" title="Der aktuell deiner Mining-Adresse zugewiesene Coinbase-Wert inklusive Transaktionsgebühren."><span class="sub" id="blockValueLabel">Aktueller Blockwert</span><b id="blockBtc">— BTC</b><span class="sub" id="blockEur">—</span></div></div><div class="sub">Blockhöhe <span id="blockHeight">—</span></div></div>
<div class="card wide"><div class="label">Mein Public-Pool-Miner</div><div class="value" id="minerHash">—</div><div class="sub" id="minerName">Worker —</div><div class="facts poolfacts"><div class="fact"><span class="sub">Best Difficulty</span><b id="minerBest">—</b></div><div class="fact"><span class="sub">Worker</span><b id="minerWorkers">—</b></div><div class="fact"><span class="sub">Solo Work</span><b id="minerWork">—</b></div><div class="fact"><span class="sub">Last Seen</span><b id="minerSeen">—</b></div></div></div>
<div class="card wide"><div class="top"><div><div class="label">Hashrate</div><div class="sub">GH/s</div></div><div class="tabs" data-chart="hashrate"><button data-r="1h" class="active">1h</button><button data-r="24h">24h</button><button data-r="7d">7d</button></div></div><div class="chart"><canvas id="hashrate"></canvas></div></div>
<div class="card wide"><div class="top"><div><div class="label">Leistung & Temperatur</div><div class="legend"><span><i class="key asic"></i>ASIC °C</span><span><i class="key vr"></i>VR °C</span><span><i class="key power"></i>Leistung W</span><span><i class="key voltage"></i>Input V</span></div></div><div class="tabs" data-chart="thermal"><button data-r="1h" class="active">1h</button><button data-r="24h">24h</button><button data-r="7d">7d</button></div></div><div class="dual"><div class="mini"><canvas id="temperature"></canvas></div><div class="mini"><canvas id="powerchart"></canvas></div></div></div>
<div class="card wide"><div class="label">Incidents</div><div class="events" id="incidents"></div></div><div class="card wide"><div class="label">Ereignisse</div><div class="events" id="events"></div></div></section></main>
<script>
const $=id=>document.getElementById(id), fmt=(v,d=1)=>v==null?'—':Number(v).toFixed(d), dur=s=>{if(s==null)return'—';let d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60),x=Math.floor(s%60);if(d)return d+'d '+h+'h '+m+'m';if(h)return h+'h '+m+'m';return m+'m '+x+'s'}, diff=v=>{if(v==null)return'—';if(v>=1e12)return(v/1e12).toFixed(2)+'T';if(v>=1e9)return(v/1e9).toFixed(2)+'G';if(v>=1e6)return(v/1e6).toFixed(2)+'M';if(v>=1e3)return(v/1e3).toFixed(2)+'K';return String(v)};
const REFRESH_MS=10000;let lastRefreshAt=0,nextRefreshAt=0;function markRefresh(){lastRefreshAt=Date.now();nextRefreshAt=lastRefreshAt+REFRESH_MS;refreshClock()}function refreshClock(){if(!lastRefreshAt){$('seen').textContent='Refresh —';return}let next=Math.max(0,Math.ceil((nextRefreshAt-Date.now())/1000)),stamp=new Date(lastRefreshAt).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit'});$('seen').textContent='Refresh '+stamp+' · nächster in '+next+'s'}
const coverage=s=>{if(!s)return'';if(s>=86400)return Math.floor(s/86400)+'d Daten';return Math.max(1,Math.floor(s/3600))+'h Daten'};
async function current(){let r=await fetch('/api/current'),x=await r.json(),d=x.data||{},h=x.hashrate_history||{},st=x.state||(x.online?'ONLINE':'OFFLINE');$('dot').className='dot '+(st==='ONLINE'?'ok':'');$('state').textContent=st;$('health').textContent='STATUS: '+st;$('healthText').textContent=x.summary||'—';$('seen').textContent=x.age_seconds==null?'':'vor '+Math.round(x.age_seconds)+'s';$('ver').textContent=(d.version||'AxeOS')+' · Board '+(d.boardVersion||'—');$('hash').textContent=fmt(d.hashRate/1000,2)+' TH/s';$('hash10m').textContent=fmt(d.hashRate_10m/1000,2);$('hash1h').textContent=fmt(d.hashRate_1h/1000,2);$('hash24h').textContent=fmt(h.avg_24h/1000,2);$('hash7d').textContent=fmt(h.avg_7d/1000,2);$('cover24h').textContent=h.complete_24h?'':coverage(h.coverage_24h);$('cover7d').textContent=h.complete_7d?'':coverage(h.coverage_7d);$('power').textContent=fmt(d.power)+' W';$('voltage').textContent=fmt(d.voltage/1000,2)+' V · '+fmt(d.calculatedCurrent,2)+' A berechnet';$('temp').textContent=fmt(d.temp)+' °C';$('vr').textContent='VR '+fmt(d.vrTemp)+' °C · '+fmt(d.fanrpm,0)+' RPM';$('shares').textContent=(d.sharesAccepted??'—')+' / '+(d.sharesRejected??'—');$('best').textContent='Best '+diff(d.bestDiff)+' · Reject '+fmt(d.rejectRate,2)+'%';$('pool').textContent=d.isUsingFallbackStratum?'FALLBACK':'PRIMÄR';$('errors').textContent='Fehler '+fmt(d.errorPercentage,2)+'% · '+fmt(d.responseTime,0)+' ms';$('uptime').textContent=dur(d.uptimeSeconds);$('wifi').textContent=(d.wifiStatus||'—')+' · '+(d.wifiRSSI??'—')+' dBm';$('healthDetails').innerHTML=[['Mining',d.miningPaused?'pausiert':'aktiv'],['Power Fault',d.power_fault||'nein'],['Reset',d.resetReason||'—'],['Frequenz',fmt(d.actualFrequency,0)+' MHz'],['Erwartete Hashrate',fmt(d.expectedHashrate/1000,3)+' TH/s'],['Core',fmt(d.coreVoltageActual,0)+' mV']].map(v=>'<span>'+v[0]+': <b>'+v[1]+'</b></span>').join('')}
const eur=v=>v==null?'—':new Intl.NumberFormat('de-DE',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(v), rate=v=>{if(v==null)return'—';if(v>=1e18)return(v/1e18).toFixed(2)+' EH/s';if(v>=1e15)return(v/1e15).toFixed(2)+' PH/s';if(v>=1e12)return(v/1e12).toFixed(2)+' TH/s';return diff(v)+' H/s'};
async function market(){let r=await fetch('/api/market'),x=await r.json(),m=x.miner||{},w=m.workers?.[0]||{},p=x.price_history||[],b=x.block_value||{},chg=x.price_change_pct,color=(chg??0)>=0?'#40e0a0':'#ff5964';$('blockSubsidy').textContent=b.subsidy_btc==null?'— BTC':Number(b.subsidy_btc).toFixed(4)+' BTC';$('blockFees').textContent=b.fees_btc==null?'nicht verfügbar':Number(b.fees_btc).toFixed(8)+' BTC';$('blockBtc').textContent=b.miner_btc==null?'— BTC':Number(b.miner_btc).toFixed(b.coinbase_available?8:4)+' BTC';$('blockValueLabel').textContent=b.coinbase_available?'Aktueller Blockwert':'Blockwert ohne aktuelle Transaktionsgebühren';$('btcEur').textContent=eur(x.btc_eur);$('priceUpdated').textContent='BTC/EUR Spot · Coinbase · '+(x.updated?new Date(x.updated*1000).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}):'nicht verfügbar');$('priceChange').textContent=chg==null?'24h —':'24h '+(chg>=0?'+':'')+Number(chg).toFixed(2)+'%';$('priceChange').className='pricechange '+((chg??0)>=0?'up':'down');$('blockEur').textContent=b.miner_eur==null?'—':'≈ '+eur(b.miner_eur);$('blockHeight').textContent=b.height?.toLocaleString('de-DE')||'—';draw('btcPriceChart',[{values:p.map(v=>v.close),color:color,width:2.5}],{decimals:0,unit:'€'});$('minerHash').textContent=rate(m.hashRate);$('minerName').textContent='Worker '+(w.name||'—')+' · '+String(w.payoutMode||'solo').toUpperCase();$('minerBest').textContent=diff(m.bestDifficulty);$('minerWorkers').textContent=m.workersCount??'—';$('minerWork').textContent=diff(m.soloWork);$('minerSeen').textContent=m.lastSeen?new Date(m.lastSeen).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}):'—'}
function draw(id,series,opt={}){let c=$(id),ctx=c.getContext('2d'),w=c.clientWidth,h=c.clientHeight,d=devicePixelRatio,left=42,right=8,top=9,bottom=18;c.width=w*d;c.height=h*d;ctx.scale(d,d);ctx.clearRect(0,0,w,h);let vals=series.flatMap(s=>s.values.filter(v=>v!=null));if(!vals.length){ctx.fillStyle='#8390a3';ctx.fillText('Noch keine Verlaufsdaten',left,26);return}let min=opt.min??Math.min(...vals),max=opt.max??Math.max(...vals);if(min===max){min-=1;max+=1}ctx.font='11px system-ui';ctx.strokeStyle='#263447';ctx.fillStyle='#8390a3';ctx.lineWidth=1;for(let i=0;i<3;i++){let y=top+i*(h-top-bottom)/2,v=max-i*(max-min)/2;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();ctx.fillText(v.toFixed(opt.decimals??0)+(opt.unit||''),2,y+4)}(opt.markers||[]).forEach(m=>{let span=Math.max(1,opt.end-opt.start),x1=left+(m.started_at-opt.start)*(w-left-right)/span,x2=left+((m.ended_at||opt.end)-opt.start)*(w-left-right)/span;ctx.fillStyle='#ff596426';ctx.fillRect(Math.max(left,x1),top,Math.max(2,x2-x1),h-top-bottom)});series.forEach(s=>{ctx.strokeStyle=s.color;ctx.lineWidth=s.width||2;ctx.setLineDash(s.dash||[]);ctx.beginPath();let started=false;s.values.forEach((v,i)=>{if(v==null)return;let x=left+i*(w-left-right)/Math.max(1,s.values.length-1),y=top+(max-v)*(h-top-bottom)/(max-min);started?ctx.lineTo(x,y):(ctx.moveTo(x,y),started=true)});ctx.stroke()});ctx.setLineDash([])}
async function history(range='1h'){let r=await fetch('/api/history?range='+range),x=await r.json(),s=x.samples||[],o={markers:x.incidents||[],start:s.length?s[0].ts:0,end:s.length?s[s.length-1].ts:1};draw('hashrate',[{values:s.map(v=>v.hashrate),color:'#40e0a0'}],{...o,decimals:0});draw('temperature',[{values:s.map(v=>v.temp),color:'#ff5964',width:2.5},{values:s.map(v=>v.vr_temp),color:'#ffc857',width:2.5,dash:[7,5]}],{...o,min:20,max:85,unit:'°',decimals:0});draw('powerchart',[{values:s.map(v=>v.power),color:'#57a6ff'},{values:s.map(v=>v.voltage),color:'#40e0a0'}],{...o,min:0,max:40,decimals:1})}
async function events(){let r=await fetch('/api/events'),x=await r.json();$('events').innerHTML=x.length?x.map(e=>'<div class="event"><span>'+new Date(e.ts*1000).toLocaleString()+'</span><b class="sev-'+e.severity+'">'+e.kind+'</b><span>'+e.message+'</span></div>').join(''):'<div class="sub" style="padding-top:12px">Noch keine Ereignisse</div>'}
async function incidents(){let r=await fetch('/api/incidents'),x=await r.json();$('incidents').innerHTML=x.length?x.map(i=>{let end=i.ended_at||Math.floor(Date.now()/1000),b=i.before_sample||{};return '<div class="event"><span>'+new Date(i.started_at*1000).toLocaleString()+'<br><small>'+dur(end-i.started_at)+'</small></span><b class="sev-'+i.severity+'">'+i.kind+'</b><span>'+i.summary+'<br><small>Vorher: '+fmt(b.voltage/1000,2)+' V · '+fmt(b.power,1)+' W · '+fmt(b.hashRate/1000,2)+' TH/s</small></span></div>'}).join(''):'<div class="sub" style="padding-top:12px">Keine Vorfälle</div>'}
async function refreshDashboard(){nextRefreshAt=Date.now()+REFRESH_MS;try{await current();markRefresh()}catch(e){refreshClock()}}document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.tabs button[data-r="'+b.dataset.r+'"]').forEach(x=>x.classList.add('active'));history(b.dataset.r)});refreshDashboard();market();history();events();incidents();setInterval(()=>{refreshDashboard();history(document.querySelector('.tabs button.active').dataset.r);events();incidents()},REFRESH_MS);setInterval(refreshClock,1000);setInterval(market,60000);
</script></body></html>'''


def now():
    return int(time.time())


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


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


def merge_intervals(intervals, start, end):
    clipped = sorted((max(start, a), min(end, b)) for a, b in intervals if b > start and a < end)
    merged = []
    for left, right in clipped:
        if not merged or left > merged[-1][1]:
            merged.append([left, right])
        else:
            merged[-1][1] = max(merged[-1][1], right)
    return [tuple(value) for value in merged]


def time_weighted_hashrate(samples, start, end, offline_intervals=(), carry_seconds=None):
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
    covered = measured_seconds + offline_seconds - overlap_seconds
    return {"average": None if not covered else work / covered, "coverage": int(covered)}


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
        for label, seconds in (("24h", 86400), ("7d", 604800)):
            start = end - seconds
            query_start = start - POLL_SECONDS * 3
            rows = con.execute("SELECT ts,hashrate FROM samples WHERE ts>=? AND ts<=? ORDER BY ts",
                               (query_start, end)).fetchall()
            downtime = confirmed_downtime(con, start, end)
            measured_start = max(start, first) if first is not None else end
            calculation = time_weighted_hashrate(
                [(row["ts"], row["hashrate"]) for row in rows], measured_start, end,
                downtime, POLL_SECONDS * 3)
            coverage = calculation["coverage"]
            result[f"avg_{label}"] = calculation["average"]
            result[f"coverage_{label}"] = coverage
            result[f"complete_{label}"] = coverage >= seconds - POLL_SECONDS * 3
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


def safe_payload(data):
    payload = dict(data or {})
    payload["calculatedCurrent"] = calculated_current(payload)
    return payload


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


def attach_incident_sample(incident_id, ts, phase, data):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO incident_samples VALUES(?,?,?,?)",
                    (incident_id, ts, phase, json.dumps(safe_payload(data), separators=(",", ":"))))


def get_sample_window(start, end):
    with db() as con:
        rows = con.execute("SELECT ts,payload FROM samples WHERE ts BETWEEN ? AND ? ORDER BY ts",
                           (start, end)).fetchall()
    return [safe_payload(json.loads(r["payload"])) | {"ts": r["ts"]} for r in rows]


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


def add_event(kind, severity, message, ts=None):
    with db() as con:
        cur = con.execute("INSERT INTO events(ts,kind,severity,message) VALUES(?,?,?,?)",
                          (ts or now(), kind, severity, message))
        return cur.lastrowid


def update_event(event_id, kind, severity, message):
    with db() as con:
        con.execute("UPDATE events SET kind=?,severity=?,message=? WHERE id=?",
                    (kind, severity, message, event_id))


def clean(raw):
    return {k: raw.get(k) for k in ALLOWED}


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
    if data.get("miningPaused"):
        return "Mining pausiert"
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
    if state == "ONLINE":
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


def detect(old, new):
    if not old:
        add_event("START", "info", "Monitoring gestartet")
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
    with db() as con:
        active = con.execute("SELECT id,started_at,before_sample FROM incidents WHERE status='ACTIVE' ORDER BY id DESC LIMIT 1").fetchone()
    if active:
        incident_id, incident_start = active["id"], active["started_at"]
        incident_before = json.loads(active["before_sample"]) if active["before_sample"] else previous()
    while True:
        started = time.monotonic()
        try:
            req = urllib.request.Request(API_URL, headers={"Accept": "application/json", "User-Agent": "BitaxeMonitor/1.0"})
            with urllib.request.urlopen(req, timeout=6) as res:
                raw = json.load(res)
            address = mining_address(raw)
            if address:
                with market_lock:
                    miner_address = address
            data = clean(raw)
            old = previous()
            detect(old, data)
            stamp = now()
            save(data, stamp)
            failures = 0
            successes += 1
            hashrate = data.get("hashRate") or 0
            fact = observed_cause(data)
            rebooted = bool(old and (data.get("uptimeSeconds") or 0) + 30 < (old.get("uptimeSeconds") or 0))
            stopped = (hashrate <= 10 and not data.get("miningPaused")) or bool(fact)
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
            if state in {"OFFLINE", "RECOVERING"}:
                state = "RECOVERING" if successes < RECOVERY_POLLS else "ONLINE"
            else:
                state = "DEGRADED" if stopped_polls else "ONLINE"
        except Exception as exc:
            failures += 1
            successes = 0
            state = "DEGRADED" if failures < OFFLINE_AFTER_POLLS else "OFFLINE"
            if failures == OFFLINE_AFTER_POLLS:
                offline_since = now() - POLL_SECONDS * (OFFLINE_AFTER_POLLS - 1)
                if incident_id is None:
                    incident_start = offline_since
                    incident_before = previous()
                    incident_id = create_incident(incident_start, "UNKNOWN", "GERÄT NICHT ERREICHBAR",
                                                  "API seit mehreren Polls nicht erreichbar", "critical",
                                                  facts={"controller_reachable": False}, before_override=incident_before)
                add_event("OFFLINE", "critical", "Bitaxe seit mehreren Polls nicht erreichbar")
            if incident_id:
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
            return self.send_json({"online": age < POLL_SECONDS * 3, "state": state,
                                   "age_seconds": age, "summary": summary, "data": data,
                                   "hashrate_history": cached_historical_hashrate()})
        if p.path == "/api/events":
            with db() as con:
                rows = con.execute("SELECT ts,kind,severity,message FROM events WHERE kind <> 'HASHRATE' ORDER BY ts DESC LIMIT 100").fetchall()
            return self.send_json([dict(r) for r in rows])
        if p.path == "/api/incidents":
            with db() as con:
                rows = con.execute("SELECT * FROM incidents ORDER BY started_at DESC LIMIT 100").fetchall()
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
                samples = con.execute("SELECT ts,phase,payload FROM incident_samples WHERE incident_id=? ORDER BY ts", (incident_id,)).fetchall()
            if not row:
                return self.send_json({"error": "not found"}, 404)
            item = dict(row)
            for key in ("facts", "before_sample", "after_sample", "pre_stats"):
                item[key] = json.loads(item[key]) if item.get(key) else None
            item["samples"] = [{"ts": s["ts"], "phase": s["phase"], **json.loads(s["payload"])} for s in samples]
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
            with db() as con:
                rows = con.execute("""SELECT (ts/?)*? ts,AVG(hashrate) hashrate,AVG(power) power,AVG(temp) temp,
                    AVG(voltage)/1000.0 voltage,AVG(vr_temp) vr_temp
                    FROM samples WHERE ts>=? GROUP BY (ts/?) ORDER BY ts""", (bucket, bucket, now()-seconds, bucket)).fetchall()
                markers = con.execute("SELECT id,started_at,ended_at,kind FROM incidents WHERE started_at>=? ORDER BY started_at", (now()-seconds,)).fetchall()
            return self.send_json({"samples": [dict(r) for r in rows], "incidents": [dict(r) for r in markers]})
        self.send_error(404)


if __name__ == "__main__":
    init_db()
    backfill_historical_incidents()
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=market_poller, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
