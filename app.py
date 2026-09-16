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
PUBLIC_POOL_API_URL = os.getenv("PUBLIC_POOL_API_URL", "https://public-pool.io:40557/api").rstrip("/")
BTC_PRICE_URL = os.getenv("BTC_PRICE_URL", "https://api.coinbase.com/v2/prices/BTC-EUR/spot")
MARKET_SECONDS = max(60, int(os.getenv("MARKET_SECONDS", "300")))

market_cache = {"updated": None, "btc_eur": None, "block_btc": None, "miner": {}, "network": {}}
market_lock = threading.Lock()
miner_address = None

ALLOWED = (
    "power", "voltage", "current", "temp", "vrTemp", "coreVoltageActual",
    "actualFrequency", "hashRate", "hashRate_1m", "hashRate_10m", "hashRate_1h",
    "errorPercentage", "sharesAccepted", "sharesRejected", "bestDiff",
    "bestSessionDiff", "poolDifficulty", "networkDifficulty", "responseTime",
    "uptimeSeconds", "totalUptimeSeconds", "resetReason", "blockFound",
    "overheat_mode", "wifiStatus", "wifiRSSI", "miningPaused",
    "isUsingFallbackStratum", "version", "boardVersion", "fanrpm"
)

HTML = r'''<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bitaxe Monitor</title>
<style>
:root{color-scheme:dark;--bg:#070a0f;--card:#101620;--muted:#8390a3;--text:#f3f6fb;--green:#40e0a0;--yellow:#ffc857;--red:#ff5964;--blue:#57a6ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 70% -20%,#172638,#070a0f 45%);color:var(--text);font:15px system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:1600px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;gap:20px;align-items:center}.brand{font-size:clamp(23px,3vw,40px);font-weight:800;letter-spacing:.03em}.status{display:flex;gap:9px;align-items:center;color:var(--muted)}.dot{width:11px;height:11px;border-radius:50%;background:var(--red);box-shadow:0 0 18px currentColor}.dot.ok{background:var(--green)}.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:20px 0}.card{background:linear-gradient(145deg,#121a25,#0d121a);border:1px solid #202b3a;border-radius:16px;padding:16px;min-width:0;box-shadow:0 10px 35px #0005}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.1em}.value{font-size:clamp(22px,2.4vw,38px);font-weight:750;margin-top:7px;white-space:nowrap}.sub{color:var(--muted);margin-top:4px;overflow:hidden;text-overflow:ellipsis}.wide{grid-column:span 3}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}.facts.poolfacts{grid-template-columns:repeat(4,1fr)}.fact{background:#0b1119;border-radius:10px;padding:10px}.fact b{display:block;font-size:18px;margin-top:3px}.chart{height:230px;position:relative}.chart canvas{width:100%;height:190px}.dual{height:230px;display:grid;grid-template-rows:1fr 1fr;gap:8px;margin-top:4px}.mini{min-height:0;position:relative}.mini canvas{width:100%;height:94px}.legend{display:flex;gap:16px;align-items:center;color:var(--muted);font-size:12px}.key{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}.key.temp{background:var(--red)}.key.power{background:var(--blue)}.tabs{display:flex;gap:7px}.tabs button{background:#182231;color:#bac5d4;border:0;border-radius:8px;padding:6px 12px;cursor:pointer}.tabs button.active{background:var(--blue);color:#04101d}.events{max-height:310px;overflow:auto}.event{display:grid;grid-template-columns:145px 110px 1fr;gap:12px;padding:10px 0;border-bottom:1px solid #202b3a}.sev-warning{color:var(--yellow)}.sev-critical{color:var(--red)}.sev-info{color:var(--green)}@media(max-width:1050px){.grid{grid-template-columns:repeat(3,1fr)}.wide{grid-column:span 3}.facts.poolfacts{grid-template-columns:repeat(2,1fr)}}@media(max-width:620px){.wrap{padding:13px}.grid{grid-template-columns:1fr 1fr}.wide{grid-column:span 2}.facts,.facts.poolfacts{grid-template-columns:1fr}.event{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}.chart{height:210px}.dual{height:220px}}
</style></head><body><main class="wrap"><div class="top"><div><div class="brand">₿ BITAXE GAMMA 601</div><div class="sub" id="ver">AxeOS</div></div><div class="status"><span class="dot" id="dot"></span><b id="state">WARTE AUF DATEN</b><span id="seen"></span></div></div>
<section class="grid"><div class="card"><div class="label">Hashrate</div><div class="value" id="hash">—</div><div class="sub" id="hashSub">—</div></div><div class="card"><div class="label">Leistung</div><div class="value" id="power">—</div><div class="sub" id="voltage">—</div></div><div class="card"><div class="label">ASIC / VR</div><div class="value" id="temp">—</div><div class="sub" id="vr">—</div></div><div class="card"><div class="label">Shares</div><div class="value" id="shares">—</div><div class="sub" id="best">—</div></div><div class="card"><div class="label">Pool / Fehler</div><div class="value" id="pool">—</div><div class="sub" id="errors">—</div></div><div class="card"><div class="label">Laufzeit</div><div class="value" id="uptime">—</div><div class="sub" id="wifi">—</div></div>
<div class="card wide"><div class="label">Blockwert</div><div class="value" id="blockBtc">— BTC</div><div class="facts"><div class="fact"><span class="sub">1 Bitcoin</span><b id="btcEur">—</b></div><div class="fact"><span class="sub">1 Block (ohne Gebühren)</span><b id="blockEur">—</b></div><div class="fact"><span class="sub">Blockhöhe</span><b id="blockHeight">—</b></div></div></div>
<div class="card wide"><div class="label">Mein Public-Pool-Miner</div><div class="value" id="minerHash">—</div><div class="sub" id="minerName">Worker —</div><div class="facts poolfacts"><div class="fact"><span class="sub">Best Difficulty</span><b id="minerBest">—</b></div><div class="fact"><span class="sub">Worker</span><b id="minerWorkers">—</b></div><div class="fact"><span class="sub">Solo Work</span><b id="minerWork">—</b></div><div class="fact"><span class="sub">Last Seen</span><b id="minerSeen">—</b></div></div></div>
<div class="card wide"><div class="top"><div><div class="label">Hashrate</div><div class="sub">GH/s</div></div><div class="tabs" data-chart="hashrate"><button data-r="1h" class="active">1h</button><button data-r="24h">24h</button><button data-r="7d">7d</button></div></div><div class="chart"><canvas id="hashrate"></canvas></div></div>
<div class="card wide"><div class="top"><div><div class="label">Leistung & Temperatur</div><div class="legend"><span><i class="key temp"></i>Temperatur °C</span><span><i class="key power"></i>Leistung W</span></div></div><div class="tabs" data-chart="thermal"><button data-r="1h" class="active">1h</button><button data-r="24h">24h</button><button data-r="7d">7d</button></div></div><div class="dual"><div class="mini"><canvas id="temperature"></canvas></div><div class="mini"><canvas id="powerchart"></canvas></div></div></div>
<div class="card wide"><div class="label">Ereignisse</div><div class="events" id="events"></div></div><div class="card wide"><div class="label">Gerätestatus</div><div id="detail" style="line-height:2;margin-top:8px"></div></div></section></main>
<script>
const $=id=>document.getElementById(id), fmt=(v,d=1)=>v==null?'—':Number(v).toFixed(d), dur=s=>{if(s==null)return'—';let d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return(d?d+'d ':'')+h+'h '+m+'m'}, diff=v=>{if(v==null)return'—';if(v>=1e12)return(v/1e12).toFixed(2)+'T';if(v>=1e9)return(v/1e9).toFixed(2)+'G';if(v>=1e6)return(v/1e6).toFixed(2)+'M';if(v>=1e3)return(v/1e3).toFixed(2)+'K';return String(v)};
async function current(){let r=await fetch('/api/current'),x=await r.json(),d=x.data||{};$('dot').className='dot '+(x.online?'ok':'');$('state').textContent=x.online?'ONLINE':'OFFLINE';$('seen').textContent=x.age_seconds==null?'':'vor '+Math.round(x.age_seconds)+'s';$('ver').textContent=(d.version||'AxeOS')+' · Board '+(d.boardVersion||'—');$('hash').textContent=fmt(d.hashRate/1000,2)+' TH/s';$('hashSub').textContent='1m '+fmt(d.hashRate_1m/1000,2)+' · 1h '+fmt(d.hashRate_1h/1000,2);$('power').textContent=fmt(d.power)+' W';$('voltage').textContent=fmt(d.voltage/1000,2)+' V · '+fmt(d.current/1000,1)+' A';$('temp').textContent=fmt(d.temp)+' °C';$('vr').textContent='VR '+fmt(d.vrTemp)+' °C · '+fmt(d.fanrpm,0)+' RPM';$('shares').textContent=(d.sharesAccepted??'—')+' / '+(d.sharesRejected??'—');$('best').textContent='Best '+diff(d.bestDiff);$('pool').textContent=d.isUsingFallbackStratum?'FALLBACK':'PRIMÄR';$('errors').textContent='Fehler '+fmt(d.errorPercentage,2)+'% · '+fmt(d.responseTime,0)+' ms';$('uptime').textContent=dur(d.uptimeSeconds);$('wifi').textContent=(d.wifiStatus||'—')+' · '+(d.wifiRSSI??'—')+' dBm';$('detail').innerHTML=['Mining: '+(d.miningPaused?'pausiert':'aktiv'),'Overheat: '+(d.overheat_mode?'JA':'nein'),'Reset: '+(d.resetReason||'—'),'Frequenz: '+fmt(d.actualFrequency,0)+' MHz','Core: '+fmt(d.coreVoltageActual,0)+' mV'].map(v=>'<div>'+v+'</div>').join('')}
const eur=v=>v==null?'—':new Intl.NumberFormat('de-DE',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(v), rate=v=>{if(v==null)return'—';if(v>=1e18)return(v/1e18).toFixed(2)+' EH/s';if(v>=1e15)return(v/1e15).toFixed(2)+' PH/s';if(v>=1e12)return(v/1e12).toFixed(2)+' TH/s';return diff(v)+' H/s'};
async function market(){let r=await fetch('/api/market'),x=await r.json(),m=x.miner||{},w=m.workers?.[0]||{};$('blockBtc').textContent=x.block_btc==null?'— BTC':Number(x.block_btc).toFixed(4)+' BTC';$('btcEur').textContent=eur(x.btc_eur);$('blockEur').textContent=eur(x.block_eur);$('blockHeight').textContent=x.network?.blocks?.toLocaleString('de-DE')||'—';$('minerHash').textContent=rate(m.hashRate);$('minerName').textContent='Worker '+(w.name||'—')+' · '+String(w.payoutMode||'solo').toUpperCase();$('minerBest').textContent=diff(m.bestDifficulty);$('minerWorkers').textContent=m.workersCount??'—';$('minerWork').textContent=diff(m.soloWork);$('minerSeen').textContent=m.lastSeen?new Date(m.lastSeen).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}):'—'}
function draw(id,series,opt={}){let c=$(id),ctx=c.getContext('2d'),w=c.clientWidth,h=c.clientHeight,d=devicePixelRatio,left=42,right=8,top=9,bottom=18;c.width=w*d;c.height=h*d;ctx.scale(d,d);ctx.clearRect(0,0,w,h);let vals=series.flatMap(s=>s.values.filter(v=>v!=null));if(!vals.length){ctx.fillStyle='#8390a3';ctx.fillText('Noch keine Verlaufsdaten',left,26);return}let min=opt.min??Math.min(...vals),max=opt.max??Math.max(...vals);if(min===max){min-=1;max+=1}ctx.font='11px system-ui';ctx.strokeStyle='#263447';ctx.fillStyle='#8390a3';ctx.lineWidth=1;for(let i=0;i<3;i++){let y=top+i*(h-top-bottom)/2,v=max-i*(max-min)/2;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();ctx.fillText(v.toFixed(opt.decimals??0)+(opt.unit||''),2,y+4)}series.forEach(s=>{ctx.strokeStyle=s.color;ctx.lineWidth=2;ctx.beginPath();let started=false;s.values.forEach((v,i)=>{if(v==null)return;let x=left+i*(w-left-right)/Math.max(1,s.values.length-1),y=top+(max-v)*(h-top-bottom)/(max-min);started?ctx.lineTo(x,y):(ctx.moveTo(x,y),started=true)});ctx.stroke()})}
async function history(range='1h'){let r=await fetch('/api/history?range='+range),x=await r.json();draw('hashrate',[{values:x.map(v=>v.hashrate),color:'#40e0a0'}],{unit:'',decimals:0});draw('temperature',[{values:x.map(v=>v.temp),color:'#ff5964'}],{min:40,max:80,unit:'°',decimals:0});draw('powerchart',[{values:x.map(v=>v.power),color:'#57a6ff'}],{min:0,max:40,unit:'W',decimals:0})}
async function events(){let r=await fetch('/api/events'),x=await r.json();$('events').innerHTML=x.length?x.map(e=>'<div class="event"><span>'+new Date(e.ts*1000).toLocaleString()+'</span><b class="sev-'+e.severity+'">'+e.kind+'</b><span>'+e.message+'</span></div>').join(''):'<div class="sub" style="padding-top:12px">Noch keine Ereignisse</div>'}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.tabs button[data-r="'+b.dataset.r+'"]').forEach(x=>x.classList.add('active'));history(b.dataset.r)});current();market();history();events();setInterval(()=>{current();history(document.querySelector('.tabs button.active').dataset.r);events()},10000);setInterval(market,60000);
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
        """)


def add_event(kind, severity, message, ts=None):
    with db() as con:
        con.execute("INSERT INTO events(ts,kind,severity,message) VALUES(?,?,?,?)",
                    (ts or now(), kind, severity, message))


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
            subsidy = block_subsidy(safe_network["blocks"])
            with market_lock:
                market_cache = {"updated": now(), "btc_eur": btc_eur, "block_btc": subsidy,
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
    if (new.get("uptimeSeconds") or 0) + 30 < (old.get("uptimeSeconds") or 0):
        add_event("REBOOT", "warning", "Neustart erkannt: " + str(new.get("resetReason") or "unbekannt"))
    checks = [
        ("POWER", (old.get("power") or 0) <= POWER_HIGH < (new.get("power") or 0), "warning", f"Leistung über {POWER_HIGH:g} W"),
        ("TEMPERATURE", (old.get("temp") or 0) <= TEMP_HIGH < (new.get("temp") or 0), "critical", f"Temperatur über {TEMP_HIGH:g} °C"),
        ("HASHRATE", (old.get("hashRate") or HASHRATE_LOW) >= HASHRATE_LOW > (new.get("hashRate") or 0), "warning", f"Hashrate unter {HASHRATE_LOW:g} GH/s"),
        ("FALLBACK_POOL", not bool(old.get("isUsingFallbackStratum")) and bool(new.get("isUsingFallbackStratum")), "warning", "Fallback-Pool aktiv"),
        ("BLOCK_FOUND", (new.get("blockFound") or 0) > (old.get("blockFound") or 0), "critical", "Block-Fund gemeldet"),
        ("OVERHEAT", not bool(old.get("overheat_mode")) and bool(new.get("overheat_mode")), "critical", "Überhitzungsschutz aktiv"),
    ]
    for kind, fired, severity, message in checks:
        if fired:
            add_event(kind, severity, message)
    if (new.get("sharesRejected") or 0) > (old.get("sharesRejected") or 0):
        add_event("REJECTED_SHARE", "warning", f"Rejected Shares: {old.get('sharesRejected') or 0} → {new.get('sharesRejected')}")


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
    was_online = None
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
            save(data, now())
            if was_online is False:
                add_event("RECOVERED", "info", "Bitaxe wieder erreichbar")
            was_online = True
        except Exception as exc:
            if was_online is not False:
                add_event("OFFLINE", "critical", "Bitaxe nicht erreichbar")
            was_online = False
            print("poll failed:", type(exc).__name__, flush=True)
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
            if not row:
                return self.send_json({"online": False, "age_seconds": None, "data": {}})
            age = now() - row["ts"]
            return self.send_json({"online": age < POLL_SECONDS * 3, "age_seconds": age, "data": json.loads(row["payload"])})
        if p.path == "/api/events":
            with db() as con:
                rows = con.execute("SELECT ts,kind,severity,message FROM events ORDER BY ts DESC LIMIT 100").fetchall()
            return self.send_json([dict(r) for r in rows])
        if p.path == "/api/market":
            with market_lock:
                payload = dict(market_cache)
                payload["miner"] = dict(market_cache["miner"])
                payload["miner"]["workers"] = [dict(w) for w in market_cache["miner"].get("workers", [])]
                payload["network"] = dict(market_cache["network"])
            payload["block_eur"] = None if payload["btc_eur"] is None or payload["block_btc"] is None else payload["btc_eur"] * payload["block_btc"]
            return self.send_json(payload)
        if p.path == "/api/history":
            ranges = {"1h": 3600, "24h": 86400, "7d": 604800}
            seconds = ranges.get(parse_qs(p.query).get("range", ["1h"])[0], 3600)
            bucket = max(10, seconds // 600)
            with db() as con:
                rows = con.execute("""SELECT (ts/?)*? ts,AVG(hashrate) hashrate,AVG(power) power,AVG(temp) temp
                    FROM samples WHERE ts>=? GROUP BY (ts/?) ORDER BY ts""", (bucket, bucket, now()-seconds, bucket)).fetchall()
            return self.send_json([dict(r) for r in rows])
        self.send_error(404)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=market_poller, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
