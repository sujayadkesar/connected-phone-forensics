"""Self-contained HTML report.

One file, no external resources: forensic workstations are often offline,
and a report must render the same years later. Device cards and coverage are
rendered statically (they print); the timeline is rendered by a small inline
script with filtering, because it can hold tens of thousands of rows.
"""

from __future__ import annotations

import json
from collections import Counter
from html import escape
from pathlib import Path

from ..engine import ScanResult
from ..plugin import ERROR, UNRECOGNISED
from ..timeutil import iso
from .structured import _jsonable, iter_records

_CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1f23;--muted:#5d636e;--line:#e2e2de;--accent:#2952cc;
--ok:#1f7a4d;--warn:#a15c00;--bad:#b42318;--chip:#eef1f8;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--panel:#1b1e23;--ink:#e6e7ea;--muted:#9aa1ad;
--line:#2c3038;--accent:#8aa8ff;--ok:#5cc596;--warn:#e0a44a;--bad:#ff7b72;--chip:#232833}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:28px 32px 18px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{margin:0;font-size:22px;letter-spacing:-.01em}h2{font-size:17px;margin:32px 0 12px}
h3{font-size:15px;margin:0}.muted{color:var(--muted)}main{padding:0 32px 48px;max-width:1400px}
nav{display:flex;gap:18px;margin-top:12px;font-size:13px}nav a{color:var(--accent);text-decoration:none}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-top:16px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat b{display:block;font-size:22px}.card{background:var(--panel);border:1px solid var(--line);
border-radius:12px;padding:18px 20px;margin:14px 0;break-inside:avoid}
.card-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}.badge{font-size:11px;
padding:2px 8px;border-radius:99px;background:var(--chip);color:var(--muted);font-weight:600;
text-transform:uppercase;letter-spacing:.04em}.badge.ok{color:var(--ok)}.badge.warn{color:var(--warn)}
.badge.bad{color:var(--bad)}table{border-collapse:collapse;width:100%;margin-top:10px;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px}td.mono,.mono{font-family:var(--mono);font-size:12px;
word-break:break-all}details{margin-top:10px}summary{cursor:pointer;color:var(--accent)}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.filters input,.filters select{
background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 8px;font:inherit}
.filters input{min-width:260px}#tl tr.data td{background:var(--chip)}#more{margin:12px 0;padding:6px 14px;
border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
.note{border-left:3px solid var(--line);padding:4px 12px;color:var(--muted);margin:10px 0}
@media print{nav,.filters,#more{display:none}body{background:#fff}.card{border-color:#ccc}}
"""

_JS = r"""
const D=JSON.parse(document.getElementById('tether-data').textContent);
const rows=D.timeline;const tb=document.querySelector('#tl tbody');const PAGE=500;let shown=0,filtered=rows;
const q=document.getElementById('q'),fd=document.getElementById('fdev'),fc=document.getElementById('fch'),fa=document.getElementById('fart');
function opt(sel,vals){for(const v of vals){const o=document.createElement('option');o.value=v;o.textContent=v;sel.appendChild(o);}}
opt(fd,[...new Set(rows.map(r=>r.device||'(none)'))].sort());opt(fc,[...new Set(rows.map(r=>r.channel))].sort());
opt(fa,[...new Set(rows.map(r=>r.artifact))].sort());
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function render(reset){if(reset){tb.innerHTML='';shown=0;}const end=Math.min(shown+PAGE,filtered.length);
const frag=document.createDocumentFragment();for(let i=shown;i<end;i++){const r=filtered[i];const tr=document.createElement('tr');
const t=(r.datetime||'').replace('T',' ').replace(/\.0+(?=\+)/,'').replace('+00:00','');
tr.innerHTML=`<td class="mono" style="white-space:nowrap">${esc(t)}</td><td>${esc(r.timestamp_desc)}${r.time_source==='filesystem'?' <span class="badge warn">file time</span>':''}</td><td>${esc(r.device_label||'')}<div class="muted mono">${esc(r.device||'')}</div></td><td>${esc(r.channel)}</td><td>${esc(r.message)}</td><td class="mono">${esc(r.source_path)}<div class="muted">${esc(r.locator)}</div></td>`;
tr.style.cursor='pointer';tr.onclick=()=>{const n=tr.nextSibling;if(n&&n.classList&&n.classList.contains('data')){n.remove();return;}
const d=document.createElement('tr');d.className='data';d.innerHTML=`<td colspan="6" class="mono">artifact: ${esc(r.artifact)} · event: ${esc(r.event)} · user: ${esc(r.user||'-')} · system: ${esc(r.system)}<br>sha256: ${esc(r.sha256||'-')}<br>ids: ${esc((r.device_ids||[]).join(', '))}<br>${esc(JSON.stringify(r.data))}</td>`;tr.after(d);};
frag.appendChild(tr);}tb.appendChild(frag);shown=end;document.getElementById('count').textContent=`${filtered.length} event(s), showing ${shown}`;
document.getElementById('more').style.display=shown<filtered.length?'inline-block':'none';}
function apply(){const t=q.value.toLowerCase();filtered=rows.filter(r=>(!fd.value||(r.device||'(none)')===fd.value)&&(!fc.value||r.channel===fc.value)&&(!fa.value||r.artifact===fa.value)&&
(!t||JSON.stringify(r).toLowerCase().includes(t)));render(true);}
[q,fd,fc,fa].forEach(e=>e.addEventListener('input',apply));document.getElementById('more').onclick=()=>render(false);render(true);
"""


def _fmt(dt) -> str:
    return iso(dt)[:19].replace("T", " ") + " UTC" if dt else "—"


def _device_cards(result: ScanResult) -> str:
    phones = [d for d in result.graph.devices if d.device_class in (None, "phone", "tablet")]
    others = [d for d in result.graph.devices if d.device_class not in (None, "phone", "tablet")]
    html = _cards(result, phones) or '<p class="note">No phones were identified in this evidence.</p>'
    if others:
        html += (f"<details><summary>Other devices ({len(others)}): headsets, computers, peripherals"
                 f"</summary>{_cards(result, others)}</details>")
    return html


def _cards(result: ScanResult, devices) -> str:
    out = []
    matches = result.graph.possible_matches
    for dev in devices:
        d = dev.to_dict()
        badge = ('<span class="badge ok">confirmed</span>' if dev.confirmed
                 else '<span class="badge warn">unconfirmed · weak identifiers only</span>')
        os_badge = f'<span class="badge">{escape(dev.phone_os)}</span>' if dev.phone_os else ""
        if dev.device_class:
            os_badge += f'<span class="badge">{escape(dev.device_class)}</span>'
        ids = "".join(
            f"<tr><td>{escape(i['kind'])}</td><td class='mono'>{escape(i['value'])}</td>"
            f"<td>{escape(i['strength'])}</td><td class='mono'>{escape(', '.join(i['sources']))}</td></tr>"
            for i in d["identifiers"])
        chans = "".join(
            f"<tr><td>{escape(c['channel'])}</td><td class='mono'>{escape(_fmt_iso(c['first_seen']))}</td>"
            f"<td class='mono'>{escape(_fmt_iso(c['last_seen']))}</td><td>{c['observations']}</td>"
            f"<td class='mono'>{escape(', '.join(c['artifacts']))}</td></tr>" for c in d["channels"])
        events = ", ".join(f"{escape(k)} ({v})" for k, v in d["events"].items())
        reasons = "".join(
            f"<li><span class='mono'>{escape(r['joined'])}</span> — asserted together by "
            f"<b>{escape(r['artifact'])}</b> in <span class='mono'>{escape(r['source_path'])}</span> "
            f"(<span class='mono'>{escape(r['locator'])}</span>)</li>" for r in d["merge_reasons"])
        pm = [m for m in matches if dev.id in m["devices"]]
        pm_html = "".join(
            f"<li>{escape(' ↔ '.join(m['devices']))}: shared {escape(m['shared']['kind'])} "
            f"<span class='mono'>{escape(m['shared']['value'])}</span> — {escape(m['note'])}</li>" for m in pm)
        out.append(f"""
<section class="card" id="{escape(dev.id)}">
  <div class="card-head"><h3>{escape(dev.label)}</h3><span class="mono muted">{escape(dev.id)}</span>{badge}{os_badge}</div>
  <div class="muted">First seen {_fmt(dev.first_seen)} · last seen {_fmt(dev.last_seen)} ·
   computers: {escape(', '.join(d['systems']) or '—')} · local users: {escape(', '.join(d['users']) or '—')}</div>
  <table><thead><tr><th>Identifier</th><th>Value</th><th>Strength</th><th>Asserted by</th></tr></thead><tbody>{ids}</tbody></table>
  <table><thead><tr><th>Channel</th><th>First seen</th><th>Last seen</th><th>Records</th><th>Artifacts</th></tr></thead><tbody>{chans}</tbody></table>
  <p class="muted">Events: {events or '—'}</p>
  {"<details><summary>How these records were linked (" + str(len(d['merge_reasons'])) + ")</summary><ul>" + reasons + "</ul></details>" if reasons else ""}
  {"<details open><summary>Possible matches with other devices</summary><ul>" + pm_html + "</ul></details>" if pm_html else ""}
</section>""")
    return "".join(out)


def _fmt_iso(s: str | None) -> str:
    return s[:19].replace("T", " ") if s else "—"


def _coverage(result: ScanResult) -> str:
    counts = Counter(c.status for c in result.coverage)
    rows = "".join(
        f"<tr><td class='mono'>{escape(c.plugin)}</td><td>{escape(c.system)}</td>"
        f"<td class='mono'>{escape(c.source_path)}</td>"
        f"<td><span class='badge {'bad' if c.status == ERROR else 'warn' if c.status == UNRECOGNISED else 'ok'}'>"
        f"{escape(c.status)}</span></td><td>{c.records}</td><td>{escape(c.note)}</td></tr>"
        for c in sorted(result.coverage, key=lambda c: (c.status not in (ERROR, UNRECOGNISED), c.plugin)))
    plugins = "".join(
        f"<tr><td class='mono'>{escape(p['id'])}</td><td>{escape(p['name'])}</td><td>{escape(p['system'])}</td>"
        f"<td>{escape(p['validation'])}</td><td>{p['files']}</td><td>{p['observations']}</td></tr>"
        for p in result.plugins_run)
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no artifact files found"
    return f"""
<p>{escape(summary)}</p>
<p class="note">Every file a parser opened is listed. <b>unrecognised</b> means the file exists but its format or
schema is not one Tether knows: it was <i>not</i> parsed and should be examined manually. Nothing is skipped silently.</p>
<table><thead><tr><th>Parser</th><th>Computer</th><th>File</th><th>Status</th><th>Records</th><th>Note</th></tr></thead>
<tbody>{rows or '<tr><td colspan="6" class="muted">none</td></tr>'}</tbody></table>
<details><summary>Parsers run and their validation level</summary>
<table><thead><tr><th>Parser</th><th>Artifact</th><th>Computer</th><th>Validation</th><th>Files</th><th>Records</th></tr></thead>
<tbody>{plugins}</tbody></table></details>"""


def write_html(result: ScanResult, path: Path) -> Path:
    timeline = [rec for obs, rec in iter_records(result) if obs.timestamp is not None]
    data = json.dumps({"timeline": timeline}, default=_jsonable, ensure_ascii=False)
    data = data.replace("</", "<\\/")
    phone_devs = [d for d in result.graph.devices if d.device_class in (None, "phone", "tablet")]
    n_dev = len(phone_devs)
    n_conf = sum(1 for d in phone_devs if d.confirmed)
    n_unrec = sum(1 for c in result.coverage if c.status in (ERROR, UNRECOGNISED))
    systems = "".join(
        f"<tr><td>{escape(s.label)}</td><td>{escape(s.os)}</td>"
        f"<td>{escape(' '.join(v for v in s.details.values()) or '—')}</td>"
        f"<td>{escape(', '.join(u.name for u in s.users) or '—')}</td></tr>" for s in result.systems)
    case = "".join(f"<span>{escape(k)}: <b>{escape(v)}</b></span> · " for k, v in result.case.items() if v)
    warnings = "".join(
        f"<li><span class='mono'>{escape(w.plugin)}</span> {escape(w.source_path or '')}: "
        f"{escape(w.message.splitlines()[0])}</li>" for w in result.warnings)
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tether report</title><style>{_CSS}</style></head><body>
<header>
  <h1>Tether report</h1>
  <div class="muted">{case}Generated {_fmt(result.finished)} by Tether {escape(result.tool_version)} ·
  input <span class="mono">{escape(result.input_path)}</span></div>
  <nav><a href="#devices">Devices</a><a href="#timeline">Timeline</a><a href="#coverage">Coverage</a>
  <a href="#method">Method</a></nav>
</header>
<main>
<div class="grid">
  <div class="stat"><b>{n_dev}</b><span class="muted">phones ({n_conf} confirmed)</span></div>
  <div class="stat"><b>{len(timeline)}</b><span class="muted">timed events</span></div>
  <div class="stat"><b>{len(result.observations)}</b><span class="muted">observations</span></div>
  <div class="stat"><b>{len(result.systems)}</b><span class="muted">computers examined</span></div>
  <div class="stat"><b>{n_unrec}</b><span class="muted">files not parsed</span></div>
</div>
<h2>Computers examined</h2>
<table><thead><tr><th>Label</th><th>OS</th><th>Version</th><th>User profiles</th></tr></thead><tbody>{systems}</tbody></table>
<h2 id="devices">Phones</h2>
{_device_cards(result)}
<h2 id="timeline">Timeline</h2>
<div class="filters"><input id="q" placeholder="Search all fields…"><select id="fdev"><option value="">All devices</option></select>
<select id="fch"><option value="">All channels</option></select><select id="fart"><option value="">All artifacts</option></select></div>
<div id="count" class="muted"></div>
<noscript><p class="note">JavaScript is off: open timeline.csv for the full timeline.</p></noscript>
<table id="tl"><thead><tr><th>Time (UTC)</th><th>Meaning</th><th>Device</th><th>Channel</th><th>Event</th><th>Source</th></tr></thead><tbody></tbody></table>
<button id="more">Show more</button>
<h2 id="coverage">Coverage</h2>
{_coverage(result)}
{"<h2>Warnings</h2><ul>" + warnings + "</ul>" if warnings else ""}
<h2 id="method">Method</h2>
<p class="note">Evidence is read-only. SQLite databases are copied together with their -wal/-shm files and the copy is
opened, so the original is never modified and committed WAL records are included. Registry hives with transaction logs
are recovered on a copy. Every evidence file read is SHA-256 hashed (see manifest.json).</p>
<p class="note">Times marked <b>file time</b> come from filesystem metadata rather than from inside the artifact; they
depend on how the evidence was acquired and are not used for a phone's first/last seen.</p>
<p class="note">Records are merged into one phone only through strong identifiers (UDID, serial, IMEI, Bluetooth/Wi-Fi MAC,
USB serial, vendor device ids) asserted together by a single record; each merge is listed under "How these records were
linked". Device names, models, phone numbers and accounts never merge records; shared values are shown as possible matches.
Each parser's validation level (synthetic, public_sample, ground_truth) is listed under Coverage.</p>
</main>
<script type="application/json" id="tether-data">{data}</script>
<script>{_JS}</script>
</body></html>"""
    path.write_text(html, encoding="utf-8")
    return path
