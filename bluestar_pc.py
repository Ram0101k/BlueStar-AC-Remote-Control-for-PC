#!/usr/bin/env python3
"""
Blue Star Smart AC - PC controller (unofficial)

How it works (reverse-engineered from the Android app v5.1.2):
  1. POST https://api-smart.bluestarindia.com/auth/login  -> session token + broker info ("mi")
  2. "mi" = base64("<aws-iot-endpoint>::<accessKey>::<secretKey>")
  3. Connect to AWS IoT over MQTT-on-WebSocket (SigV4 signed), client id "u-<session>"
  4. Subscribe  things/<id>/state/reported      (device state)
     Publish    $aws/things/<id>/shadow/update  {"state":{"desired":{...,"src":"anmq"}}}
     Publish    things/<id>/control             {"fpsh":1}   (ask device to push state now)

Install:   pip install requests paho-mqtt
Run:       python bluestar_pc.py
Then open: http://127.0.0.1:8765

Your password is only sent to Blue Star's own server and is never saved to disk.
The web page only listens on localhost.
"""
import argparse
import base64
import datetime
import getpass
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import paho.mqtt.client as mqtt

APPDIR = os.path.join(os.path.expanduser("~"), ".bluestar_pc")
os.makedirs(APPDIR, exist_ok=True)
SAVED_ID_FILE = os.path.join(APPDIR, "saved_id.txt")  # stores ONLY the phone/email, never the password

CORE_VERSION = 3  # the app checks this so an old copy of this file is noticed
LAST_DISCOVERY = ""  # one-line summary of what device discovery found (shown in the app's Log window)

API = "https://api-smart.bluestarindia.com"
APP_HEADERS = {
    "X-APP-VER": "v5.1.2-183",
    "X-OS-NAME": "Android",
    "X-OS-VER": "v14-34",
    "User-Agent": "com.bluestarindia.bluesmart",
}

# ----------------------------------------------------------------------------
# REST: login + device list
# ----------------------------------------------------------------------------

class LoginError(Exception):
    """A sign-in problem with a message that is safe to show the user as-is."""


def login(auth_id: str, password: str, auth_type=None) -> dict:
    # The app sends auth_type=1 for a 10-digit phone number. The value used for
    # email is NOT visible in the decompiled code (guessing 2). Override with --auth-type.
    if auth_type is None:
        auth_type = 1 if re.fullmatch(r"\d{10}", auth_id) else 2
    try:
        r = requests.post(
            f"{API}/auth/login",
            json={"auth_id": auth_id, "auth_type": auth_type, "password": password},
            headers=APP_HEADERS,
            timeout=20,
        )
    except requests.exceptions.Timeout:
        raise LoginError("Blue Star's server took too long to answer. Please try again.")
    except requests.exceptions.RequestException:
        raise LoginError("Can't reach Blue Star's servers. Check your internet connection and try again.")
    try:
        data = r.json()
    except ValueError:
        data = {}
    server_msg = ""
    if isinstance(data, dict):
        server_msg = str(data.get("message") or data.get("error") or data.get("msg") or "").strip()
    detail = f" (Blue Star says: {server_msg[:120]})" if server_msg else ""
    if r.status_code in (400, 401, 403, 404, 422):
        raise LoginError("Wrong phone/email or password. Please check them and try again." + detail)
    if r.status_code == 429:
        raise LoginError("Too many attempts. Please wait a few minutes and try again.")
    if r.status_code >= 500:
        raise LoginError(f"Blue Star's server had a problem (error {r.status_code}). Please try again later.")
    if r.status_code != 200 or not isinstance(data, dict) or "session" not in data or "mi" not in data:
        raise LoginError(f"Sign-in did not succeed (error {r.status_code})." + detail)
    session = data["session"]
    try:
        endpoint, access_key, secret_key = base64.b64decode(data["mi"]).decode().split("::")[:3]
    except Exception:
        raise LoginError("Signed in, but Blue Star's reply was in an unexpected format. The app may need an update.")
    return {
        "session": session,
        "endpoint": endpoint.replace("wss://", "").replace("ssl://", "").split("/")[0].split(":")[0],
        "access_key": access_key,
        "secret_key": secret_key,
        "user": data.get("user", {}),
    }


def fetch_things(session: str):
    r = requests.get(f"{API}/things", headers={**APP_HEADERS, "X-APP-SESSION": session}, timeout=20)
    try:
        data = r.json()
    except ValueError:
        raise SystemExit(f"GET /things failed: HTTP {r.status_code} {r.text[:300]}")
    with open(os.path.join(APPDIR, "last_things_response.json"), "w") as f:
        json.dump(data, f, indent=2)  # kept so the response shape can be inspected / fixed
    return r.status_code, data


def fetch_groups(session: str):
    """GET /groups - this is the call the official app's home screen is built from (one entry per room/AC)."""
    r = requests.get(f"{API}/groups", headers={**APP_HEADERS, "X-APP-SESSION": session, "X-API-VERSION": "v1"}, timeout=20)
    try:
        data = r.json()
    except ValueError:
        raise SystemExit(f"GET /groups failed: HTTP {r.status_code} {r.text[:300]}")
    with open(os.path.join(APPDIR, "last_groups_response.json"), "w") as f:
        json.dump(data, f, indent=2)  # kept so the response shape can be inspected / fixed
    return r.status_code, data


def _shape(obj, depth=3):
    """Structure of a JSON reply with all values removed (safe to share): key names, list sizes, value types."""
    if isinstance(obj, dict):
        if depth <= 0:
            return f"dict[{len(obj)}]"
        keys = list(obj)[:14]
        out = {k: _shape(obj[k], depth - 1) for k in keys}
        if len(obj) > len(keys):
            out["..."] = f"+{len(obj) - len(keys)} more keys"
        return out
    if isinstance(obj, list):
        first = _shape(obj[0], depth - 1) if obj and depth > 0 else None
        return f"list[{len(obj)}]" + (f" of {json.dumps(first)}" if first is not None else "")
    return type(obj).__name__


def extract_groups(data) -> dict:
    """{device_id: {"name": room name, "config": model_config, "all_things": [...]}} from a /groups response."""
    out = {}
    groups = data.get("groups", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    if not groups and isinstance(data, dict):  # any list of dicts that look like groups
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and ("things" in v[0] or "master_device" in v[0]):
                groups = v
                break
    for g in groups:
        if not isinstance(g, dict):
            continue
        things = g.get("things") or []
        tid = g.get("master_device") or (things[0] if things else None)
        if tid:
            out[tid] = {"name": g.get("name") or tid, "config": g.get("model_config") or {}, "all_things": list(things)}
    return out


def fetch_devices(session: str) -> dict:
    """Every AC on the account: rooms from /groups first (what the app shows), then anything extra from /things."""
    global LAST_DISCOVERY
    devices, errors, notes = {}, [], []
    try:
        status, gdata = fetch_groups(session)
        devices.update(extract_groups(gdata))
        notes.append(f"/groups HTTP {status}: {len(devices)} room(s): " + ", ".join(f"{v['name']} ({k})" for k, v in devices.items()))
        notes.append("/groups reply shape: " + json.dumps(_shape(gdata)))
    except (Exception, SystemExit) as e:
        errors.append(f"/groups: {e}")
        notes.append(f"/groups FAILED: {e}")
    try:
        status, tdata = fetch_things(session)
        covered = {t for v in devices.values() for t in v.get("all_things", [])} | set(devices)
        extra = {k: v for k, v in extract_things(tdata).items() if k not in covered}
        devices.update(extra)
        notes.append(f"/things HTTP {status}: {len(extra)} additional device(s) not in any room")
        notes.append("/things reply shape: " + json.dumps(_shape(tdata)))
    except (Exception, SystemExit) as e:
        errors.append(f"/things: {e}")
        notes.append(f"/things FAILED: {e}")
    LAST_DISCOVERY = "DISCOVERY: " + " | ".join(notes) + f" | raw responses saved in {APPDIR}"
    print(LAST_DISCOVERY)
    if not devices and errors:
        raise SystemExit("Could not list your ACs. " + " | ".join(errors))
    return devices


def extract_things(data) -> dict:
    """Return {thing_id: {"name":..., "config":...}}. Tolerant of a few possible shapes
    because the exact /things response was not visible in the decompiled code."""
    items = data
    if isinstance(data, dict):
        for key in ("things", "devices", "data", "items"):
            if key in data:
                items = data[key]
                break
    out = {}
    if isinstance(items, dict):
        for tid, v in items.items():
            v = v if isinstance(v, dict) else {}
            out[tid] = {"name": v.get("name", tid), "config": v.get("model_config") or v.get("config") or {}}
    elif isinstance(items, list):
        for v in items:
            if isinstance(v, str):
                out[v] = {"name": v, "config": {}}
            elif isinstance(v, dict):
                tid = v.get("id") or v.get("thing_id") or v.get("thingId")
                if tid:
                    out[tid] = {"name": v.get("name", tid), "config": v.get("model_config") or v.get("config") or {}}
    return out

# ----------------------------------------------------------------------------
# AWS IoT: SigV4 presigned websocket path
# ----------------------------------------------------------------------------

def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_ws_path(endpoint: str, access_key: str, secret_key: str, region=None) -> str:
    if region is None:
        m = re.search(r"\.iot\.([a-z0-9-]+)\.amazonaws", endpoint)
        region = m.group(1) if m else "ap-south-1"
    service = "iotdevicegateway"
    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{date}/{region}/{service}/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-SignedHeaders": "host",
    }
    qs = "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in sorted(params.items()))
    empty = hashlib.sha256(b"").hexdigest()
    canonical = f"GET\n/mqtt\n{qs}\nhost:{endpoint}\n\nhost\n{empty}"
    to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    k = _hmac(("AWS4" + secret_key).encode(), date)
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"/mqtt?{qs}&X-Amz-Signature={sig}"

# ----------------------------------------------------------------------------
# MQTT bridge
# ----------------------------------------------------------------------------

class Bridge:
    def __init__(self, creds: dict, things: dict):
        self.creds, self.things = creds, things
        self.state = {tid: {} for tid in things}
        self.last_raw = {}
        self.last_seen = {}
        self.connected = False
        self.log = []
        self.client = None
        self._lock = threading.Lock()

    def _note(self, msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line)
        self.log = (self.log + [line])[-60:]

    def start(self):
        c = self.creds
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"u-{c['session']}",
            transport="websockets",
        )
        self.client.tls_set()
        self.client.ws_set_options(
            path=sigv4_ws_path(c["endpoint"], c["access_key"], c["secret_key"]),
            headers={"Host": c["endpoint"]},
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        self.client.reconnect_delay_set(min_delay=2, max_delay=30)
        self.client.connect_async(c["endpoint"], 443, keepalive=30)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc, props=None):
        ok = (rc == 0) if isinstance(rc, int) else not rc.is_failure
        self.connected = ok
        self._note(f"MQTT connect: {rc}")
        if not ok:
            return
        for tid in self.things:
            client.subscribe(f"things/{tid}/state/reported", 0)
            client.subscribe(f"$aws/events/presence/+/{tid}", 0)
            # Extra diagnostics: shows whether the cloud accepts or rejects our commands.
            client.subscribe(f"$aws/things/{tid}/shadow/update/accepted", 0)
            client.subscribe(f"$aws/things/{tid}/shadow/update/rejected", 0)
            client.subscribe(f"$aws/things/{tid}/shadow/update/delta", 0)
            self.request_state(tid)

    def _on_subscribe(self, client, userdata, mid, reason_codes, props=None):
        self._note(f"SUBACK {mid}: {[str(r) for r in reason_codes]}  (anything like 'Not authorized' = broker refused that topic)")

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        c = self.creds
        self.client.ws_set_options(
            path=sigv4_ws_path(c["endpoint"], c["access_key"], c["secret_key"]),
            headers={"Host": c["endpoint"]},
        )
        self._note("MQTT disconnected - reconnecting with a freshly signed URL")

    def _on_message(self, client, userdata, msg):
        try:
            body = json.loads(msg.payload.decode())
        except Exception:
            return
        self._note(f"RECV {msg.topic}: {msg.payload.decode(errors='replace')[:300]}")
        parts = msg.topic.split("/")
        tid = next((t for t in self.things if t in parts), None)
        if not tid:
            return
        with self._lock:
            self.last_raw[tid] = body
            self.last_seen[tid] = time.strftime("%H:%M:%S")
            if "/state/reported" in msg.topic:
                rep = body
                if isinstance(body, dict) and isinstance(body.get("state"), dict):
                    rep = body["state"].get("reported", body["state"])
                if isinstance(rep, dict):
                    self.state[tid].update(rep)
            elif "presence" in msg.topic:
                self.state[tid]["_presence"] = body.get("eventType", body)

    def request_state(self, tid):
        self.client.publish(f"things/{tid}/control", json.dumps({"fpsh": 1}), 0)

    def set_desired(self, tid, desired: dict):
        if tid not in self.things:
            raise KeyError("unknown device")
        desired = dict(desired)
        if "stemp" in desired:
            cur = str(self.state.get(tid, {}).get("stemp", ""))
            val = float(desired["stemp"])
            desired["stemp"] = f"{val:g}" if (cur and "." not in cur) else f"{val:.1f}"
        # The official app adds both of these to every command (ts = UTC epoch milliseconds).
        desired["src"] = "anmq"
        desired["ts"] = int(time.time() * 1000)
        payload = json.dumps({"state": {"desired": desired}})
        self.client.publish(f"$aws/things/{tid}/shadow/update", payload, 0)
        self._note(f"SENT {tid}: {desired}")

# ----------------------------------------------------------------------------
# Web UI
# ----------------------------------------------------------------------------

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blue Star AC</title>
<style>
:root{color-scheme:light dark;--bg:#f4f6fa;--card:#fff;--fg:#14233c;--mut:#6b7a90;--acc:#0a5cd6;--bd:#d8dfea}
@media(prefers-color-scheme:dark){:root{--bg:#0e1420;--card:#18202f;--fg:#e8eefb;--mut:#8b9ab3;--acc:#5b9bff;--bd:#2a3550}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px system-ui,sans-serif;padding:20px}
.wrap{max-width:560px;margin:auto}.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:18px;margin-bottom:16px}
h1{font-size:20px;margin:0 0 14px}h2{font-size:17px;margin:0 0 4px}.mut{color:var(--mut);font-size:13px}
.big{font-size:44px;font-weight:600}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px}
button{background:var(--card);color:var(--fg);border:1px solid var(--bd);border-radius:10px;padding:9px 14px;font:inherit;cursor:pointer}
button.on{background:var(--acc);border-color:var(--acc);color:#fff}button:hover{border-color:var(--acc)}
.t button{width:46px;font-size:20px}input{background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:8px;padding:8px;width:70px}
pre{white-space:pre-wrap;font-size:12px;max-height:180px;overflow:auto;background:var(--bg);padding:8px;border-radius:8px}
</style></head><body><div class="wrap"><h1>Blue Star AC</h1><div id="status" class="mut">connecting...</div><div id="devs"></div>
<details class="card"><summary>Debug log</summary><pre id="log"></pre></details></div>
<script>
const $=s=>document.querySelector(s);
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});return r.json()}
function send(id,desired){api('/api/set',{id,desired}).then(()=>setTimeout(load,800))}
function modeVal(s){const m=s.mode;return (m&&typeof m==='object')?m.value:m}
function chips(id,key,opts,cur,mk){return Object.entries(opts).map(([k,n])=>`<button class="${String(cur)===String(k)?'on':''}" onclick='send("${id}",${JSON.stringify(mk(k))})'>${n}</button>`).join('')}
async function load(){
 const d=await api('/api/devices');$('#status').textContent=d.connected?'Connected to cloud':'Not connected to cloud yet...';
 $('#log').textContent=d.log.join('\n');
 $('#devs').innerHTML=d.devices.map(v=>{const s=v.state,id=v.id,pow=Number(s.pow)===1,st=s.stemp??'--';
  const modes=v.modes,fans=v.fans;
  return `<div class="card"><h2>${v.name}</h2><div class="mut">${id}${s._presence?' · '+s._presence:''} · ${v.last_seen?'last reply from AC '+v.last_seen:'no reply from AC yet'}</div>
  <div class="big">${st}&deg; <span class="mut">now ${s.ctemp??'--'}&deg;</span></div>
  <div class="row"><button class="${pow?'on':''}" onclick='send("${id}",{pow:1})'>On</button><button class="${!pow?'on':''}" onclick='send("${id}",{pow:0})'>Off</button>
  <input id="t${id}" placeholder="°C" type="number" step="1" style="width:60px"><button onclick='send("${id}",{stemp:Number(document.getElementById("t${id}").value)})'>Set temp</button>
  <span class="t"><button onclick='send("${id}",{stemp:String(${(parseFloat(st)||24)-1}.0)})'>&minus;</button><button onclick='send("${id}",{stemp:String(${(parseFloat(st)||24)+1}.0)})'>+</button></span></div>
  <div class="mut" style="margin-top:12px">Mode</div><div class="row">${Object.keys(modes).length?chips(id,'mode',modes,modeVal(s),k=>({mode:{value:Number(k)}})):'<span class="mut">mode list unavailable - see debug</span>'}</div>
  <div class="mut" style="margin-top:12px">Fan speed</div><div class="row">${Object.keys(fans).length?chips(id,'fspd',fans,s.fspd,k=>({fspd:Number(k)})):`<input id="f${id}" placeholder="num"><button onclick='send("${id}",{fspd:Number(document.getElementById("f${id}").value)})'>Set</button>`}</div>
  <div class="mut" style="margin-top:12px">Extras</div><div class="row">${[['turbo','Turbo'],['sleep','Sleep'],['display','Display lights']].map(([k,n])=>`<button class="${Number(s[k])===1?'on':''}" onclick='send("${id}",{${k}:${Number(s[k])===1?0:1}})'>${n}</button>`).join('')}</div>
  ${Object.keys(v.vswing).length?`<div class="mut" style="margin-top:12px">Vertical louver</div><div class="row">${chips(id,'vswing',v.vswing,s.vswing,k=>({vswing:Number(k)}))}</div>`:''}
  ${Object.keys(v.hswing).length?`<div class="mut" style="margin-top:12px">Horizontal louver</div><div class="row">${chips(id,'hswing',v.hswing,s.hswing,k=>({hswing:Number(k)}))}</div>`:''}
  <div class="row"><button onclick='api("/api/refresh",{id:"${id}"}).then(()=>setTimeout(load,800))'>Refresh</button></div>
  <details><summary class="mut">raw state</summary><pre>${JSON.stringify(s,null,1)}</pre></details></div>`}).join('')||'<div class="card">No devices found. See debug log and last_things_response.json.</div>'}
load();setInterval(load,4000);
</script></body></html>"""


def build_options(config: dict):
    modes, fans = {}, {}
    m = config.get("mode") if isinstance(config, dict) else None
    if isinstance(m, dict):
        for k, v in m.items():
            modes[k] = (v.get("name") if isinstance(v, dict) else str(v)).title()
    f = config.get("fspd") if isinstance(config, dict) else None
    if isinstance(f, dict):
        for k, v in f.items():
            fans[k] = v if isinstance(v, str) else (v.get("name", k) if isinstance(v, dict) else str(v))
    return modes, fans


def swing_options(config: dict, key: str) -> dict:
    """Louver positions from the device config: {"<value>": "<label>"}. Shape is not fully known, so be tolerant."""
    node = config.get(key) if isinstance(config, dict) else None
    ang = node.get("angles") if isinstance(node, dict) else None
    out = {}
    if isinstance(ang, dict):
        for k, v in ang.items():
            out[str(k)] = v if isinstance(v, str) else (v.get("name", str(k)) if isinstance(v, dict) else str(k))
    elif isinstance(ang, list):
        for v in ang:
            out[str(v)] = str(v)
    return out


def make_handler(bridge: Bridge):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            pin = getattr(bridge, "pin", None)
            if not pin:
                return True
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    given = base64.b64decode(h[6:]).decode().split(":", 1)[-1]
                    return hmac.compare_digest(given, pin)
                except Exception:
                    return False
            return False

        def _deny(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Blue Star AC (username: anything, password: your PIN)"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self._authorized():
                return self._deny()
            if self.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/devices":
                devs = []
                for tid, t in bridge.things.items():
                    modes, fans = build_options(t["config"])
                    devs.append({"id": tid, "name": t["name"], "state": bridge.state.get(tid, {}), "modes": modes, "fans": fans,
                                 "vswing": swing_options(t["config"], "vswing"), "hswing": swing_options(t["config"], "hswing"),
                                 "last_seen": bridge.last_seen.get(tid)})
                return self._send(200, json.dumps({"connected": bridge.connected, "devices": devs, "log": bridge.log}))
            self._send(404, "{}")

        def do_POST(self):
            if not self._authorized():
                return self._deny()
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/set":
                    bridge.set_desired(body["id"], body["desired"])
                elif self.path == "/api/refresh":
                    bridge.request_state(body["id"])
                else:
                    return self._send(404, "{}")
                self._send(200, '{"ok":true}')
            except Exception as e:  # keep the UI alive on bad input
                self._send(400, json.dumps({"ok": False, "error": str(e)}))

    return H

# ----------------------------------------------------------------------------

def gui_login(default_id=""):
    """Small sign-in window (tkinter ships with the normal Python installer). Returns dict or None."""
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:
        return None
    root.title("Blue Star AC - Sign in")
    root.resizable(False, False)
    tk.Label(root, text="Phone (10 digits) or email").grid(row=0, column=0, padx=12, pady=(12, 2), sticky="w")
    e1 = tk.Entry(root, width=36)
    e1.insert(0, default_id)
    e1.grid(row=1, column=0, padx=12)
    tk.Label(root, text="Password").grid(row=2, column=0, padx=12, pady=(8, 2), sticky="w")
    e2 = tk.Entry(root, width=36, show="*")
    e2.grid(row=3, column=0, padx=12)
    remember = tk.IntVar(value=1 if default_id else 0)
    tk.Checkbutton(root, text="Remember my phone/email (never the password)", variable=remember).grid(
        row=4, column=0, padx=12, pady=6, sticky="w")
    out = {}

    def go(*_):
        out.update(id=e1.get().strip(), pw=e2.get(), rem=remember.get())
        root.destroy()

    tk.Button(root, text="Sign in", width=12, command=go).grid(row=5, column=0, pady=(0, 12))
    root.bind("<Return>", go)
    (e2 if default_id else e1).focus_set()
    root.mainloop()
    return out or None


def lan_ip():
    import socket
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("10.255.255.255", 1))  # no packet is actually sent
        ip = sk.getsockname()[0]
        sk.close()
        return ip
    except Exception:
        return "<this-pc-ip>"


def main():
    if sys.stdout is None:  # windowed exe builds have no console
        sys.stdout = open(os.devnull, "w")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--auth-type", type=int, default=None, help="override login auth_type (phone=1; email guessed as 2)")
    ap.add_argument("--lan", action="store_true", help="also allow other devices on your home network (requires --pin)")
    ap.add_argument("--pin", default=None, help="password for the web page when using --lan")
    ap.add_argument("--no-browser", action="store_true", help="do not open any window automatically")
    ap.add_argument("--browser", action="store_true", help="use your web browser instead of the built-in app window")
    ap.add_argument("--dump", action="store_true", help="login, save /things response, and exit")
    args = ap.parse_args()

    if args.lan and not args.pin:
        raise SystemExit("--lan needs --pin <something>, otherwise anyone on your Wi-Fi could control the AC.")

    auth_id = os.environ.get("BLUESTAR_ID")
    password = os.environ.get("BLUESTAR_PASSWORD")
    if not (auth_id and password):
        saved = open(SAVED_ID_FILE).read().strip() if os.path.exists(SAVED_ID_FILE) else ""
        g = gui_login(saved)
        if g:
            auth_id, password = g["id"], g["pw"]
            if g["rem"]:
                open(SAVED_ID_FILE, "w").write(auth_id)
            elif os.path.exists(SAVED_ID_FILE):
                os.remove(SAVED_ID_FILE)
        else:  # no GUI available: ask in the console
            auth_id = input("Phone (10 digits) or email: ").strip()
            password = getpass.getpass("Password: ")

    try:
        creds = login(auth_id, password, args.auth_type)
    except LoginError as e:
        raise SystemExit(str(e))
    print("Login OK. Broker:", creds["endpoint"])
    things = fetch_devices(creds["session"])
    print(f"Found {len(things)} AC(s): {list(things)}")
    print("Raw responses saved in", APPDIR)
    for tid, t in things.items():
        cfg = t["config"] if isinstance(t["config"], dict) else {}
        print(f"  {tid}: config keys = {sorted(cfg.keys())}")
        print(f"  {tid}: vswing config = {json.dumps(cfg.get('vswing'))[:300]}")
        print(f"  {tid}: hswing config = {json.dumps(cfg.get('hswing'))[:300]}")
    if args.dump:
        return
    if not things:
        print("No devices parsed. Send me last_things_response.json from", APPDIR, "(remove tokens) and I'll fix the parser.")

    bridge = Bridge(creds, things)
    bridge.pin = args.pin if args.lan else None
    bridge.start()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, args.port), make_handler(bridge))
    print(f"Open http://127.0.0.1:{args.port}")
    if args.lan:
        print(f"Other devices on your network: http://{lan_ip()}:{args.port}  (login: any name, password = your PIN)")
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser and not args.browser:
        try:
            import webview  # pywebview: native app window using the system's Edge/WebView2
        except Exception:
            webview = None
        if webview:
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            webview.create_window("Blue Star AC", url, width=470, height=860, min_size=(380, 500))
            webview.start()  # blocks until the window is closed
            return  # closing the window ends the program
        print("pywebview not installed - falling back to your browser.")
    if not args.no_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print("Keep this window open. Close it to stop the controller.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def show_error(msg):
    """Windowed exe has no console, so errors go in a dialog."""
    print(msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("Blue Star AC", str(msg)[:1500])
        r.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            show_error(e.code)
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback
        show_error(traceback.format_exc())
