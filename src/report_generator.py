#!/usr/bin/env python3
"""
Generate an HTML report from violation assessment JSON.

Usage:
python assessment_report_generator.py --assessment /path/to/violation_assessments.json 
                                      --report_name output.html
"""

from __future__ import annotations

import base64
import html
import json
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional


class ViolationAssessmentReport:
    """Render violation assessment JSON into a readable HTML report."""

    def __init__(self, json_path: str | Path, output_path: str | Path, project_dir: str = "", model: str = "") -> None:
        self.json_path = Path(json_path)
        self.output_path = Path(output_path)
        self.project_dir = project_dir
        self.model = model
        self.data: Dict[str, Any] = {}

    def load(self) -> None:
        path = self._resolve_input_path()
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def generate(self) -> Path:
        self.load()
        html_text = self._render_html()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(html_text, encoding="utf-8")
        return self.output_path

    def _resolve_input_path(self) -> Path:
        if self.json_path.exists():
            return self.json_path
        # Common filename mismatch: assessment vs assessments
        if self.json_path.name == "RIOT-violation_assessment.json":
            alt = self.json_path.with_name("RIOT-violation_assessments.json")
            if alt.exists():
                return alt
        raise FileNotFoundError(f"Input JSON not found: {self.json_path}")

    def _render_html(self) -> str:
        correct = int(self.data.get("Correct Violations", 0))
        incorrect = int(self.data.get("Incorrect Violations", 0))
        threat_scores = self.data.get("Threat Scores") or {}
        assessments = self.data.get("Sorted Assessments") or []
        codebase_name = self._infer_codebase_name(assessments)
        submodules = self._collect_submodules(assessments, codebase_name)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in assessments:
            llm = item.get("LLM Review") or {}
            score_raw = llm.get("Threat Score")
            score_key = "Unknown"
            try:
                score_int = int(score_raw)
                if 1 <= score_int <= 10:
                    score_key = str(score_int)
            except (TypeError, ValueError):
                pass
            grouped.setdefault(score_key, []).append(item)

        total = correct + incorrect
        
        js_project_dir = str(self.project_dir).replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        js_model = str(self.model).replace("'", "\\'").replace('"', '\\"')
        
        parts: List[str] = []
        parts.append("<!doctype html>")
        parts.append("<html><head><meta charset='utf-8'>")
        parts.append(f"<script>window.LAFVT_PROJECT_DIR = '{js_project_dir}'; window.LAFVT_MODEL = '{js_model}';</script>")
        parts.append("<title>Violation Assessment Report</title>")
        parts.append(
            "<style>"
            ":root{--bg:#121212;--ink:#e5e5e5;--muted:#a1a1aa;--card:#1c1c1c;"
            "--accent:#0f766e;--accent-2:#1d4ed8;--warn:#b45309;--border:#2a2a2a;"
            "--bar:#2a2a2a;--panel:#1a1a1a;--tooltip-bg:#e5e5e5;--tooltip-ink:#121212}"
            ".theme-light{--bg:#f6f5f2;--ink:#1f2933;--muted:#6b7280;--card:#ffffff;"
            "--border:#e5e7eb;--bar:#e5e7eb;--panel:#f3f4f6;--tooltip-bg:#111827;--tooltip-ink:#ffffff}"
            "*{box-sizing:border-box}body{margin:0;font-family:Georgia,serif;background:var(--bg);color:var(--ink)}"
            "header{padding:28px 24px 8px;position:relative}h1{margin:0;font-size:28px}"
            ".sub{color:var(--muted);margin-top:6px}"
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;padding:12px 24px}"
            ".card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px}"
            ".stat{display:flex;flex-direction:column;gap:6px}"
            ".stat-label{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}"
            ".stat-value{font-size:28px;font-weight:700}"
            ".stat-bar{height:6px;border-radius:999px;background:var(--bar);overflow:hidden}"
            ".stat-bar span{display:block;height:100%;width:100%}"
            ".stat.ok .stat-bar span{background:#16a34a}"
            ".stat.bad .stat-bar span{background:#dc2626}"
            ".stat.info .stat-bar span{background:#2563eb}"
            ".section{padding:8px 24px 20px}"
            ".section h2{margin:10px 0 8px;font-size:18px}"
            "details{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px;margin-bottom:10px}"
            "summary{cursor:pointer;font-weight:600}"
            ".meta{color:var(--muted);font-size:12px;margin-top:4px}"
            ".row{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:10px}"
            ".box{border:1px solid var(--border);border-radius:8px;padding:10px;background:var(--panel)}"
            ".chart{display:grid;grid-template-columns:40px 1fr 60px;gap:8px;align-items:center}"
            ".chart-list{display:grid;grid-template-rows:repeat(10,1fr);row-gap:6px;height:100%;padding-bottom:15px}"
            ".bar{height:12px;background:linear-gradient(90deg,#0f766e,#1d4ed8);border-radius:999px}"
            ".bar-wrap{background:var(--bar);border-radius:999px;overflow:hidden;height:12px}"
            ".charts{display:flex;gap:12px;flex-wrap:wrap;align-items:stretch;width:100%}"
            ".chart-panel{flex:1 1 calc(50% - 6px);max-width:none;position:relative;padding-left:32px}"
            ".chart-label{position:absolute;left:0px;top:50%;transform:translateY(-50%) rotate(-90deg);"
            "font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}"
            ".pie-card{flex:1 1 calc(50% - 6px);max-width:none}"
            ".pie-row{display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap}"
            ".pie{width:240px;height:240px;border-radius:50%;margin:8px}"
            ".pie-legend{display:grid;grid-template-columns:repeat(1,minmax(120px,1fr));gap:6px;font-size:12px}"
            ".swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}"
            ".pie-wrap{position:relative;display:inline-block}"
            ".pie-tooltip{position:absolute;pointer-events:none;transform:translate(-50%,-110%);"
            "background:var(--tooltip-bg);color:var(--tooltip-ink);font-size:12px;padding:4px 6px;border-radius:6px;"
            "white-space:nowrap;opacity:0;transition:opacity .12s}"
            "pre{white-space:pre-wrap;background:var(--panel);border:1px solid var(--border);padding:10px;border-radius:8px}"
            "input[type='search']{width:100%;padding:10px;border-radius:10px;border:1px solid var(--border);background:var(--panel);color:var(--ink)}"
            ".theme-toggle{position:absolute;top:18px;right:24px;background:transparent;border:1px solid var(--border);"
            "color:var(--ink);padding:6px 10px;border-radius:999px;cursor:pointer}"
            ".btn-primary{background:var(--accent);color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:600;font-size:14px;transition:opacity 0.2s}"
            ".btn-primary:hover{opacity:0.9}"
            ".btn-primary:disabled{opacity:0.5;cursor:not-allowed}"
            ".fix-box{margin-top:12px;padding:12px;border:1px solid var(--accent);border-radius:8px;background:var(--bg);display:none}"
            ".fix-box.visible{display:block}"
            ".btn-stop{position:absolute;top:18px;right:140px;background:#dc2626;color:#fff;border:none;"
            "padding:6px 12px;border-radius:999px;cursor:pointer;font-size:13px;font-weight:600;transition:opacity 0.2s}"
            ".btn-stop:hover{opacity:0.85}"
            "@media (max-width:600px){header{padding:20px 16px} .section{padding:8px 16px} .grid{padding:12px 16px}}"
            "</style>"
        )
        parts.append("</head><body>")

        parts.append("<header>")
        parts.append("<h1>Violation Assessment Report</h1>")
        parts.append("<div class='sub'>Interactive summary and per-violation assessments</div>")
        if codebase_name:
            parts.append(f"<h2>Codebase: {html.escape(codebase_name)}</h2>")
        parts.append("<button id='theme-toggle' class='theme-toggle' type='button'>Light mode</button>")
        parts.append("<button class='btn-stop' onclick='stopServer()'>Stop Server ⏹</button>")
        parts.append("</header>")

        parts.append("<div class='grid'>")
        parts.append(self._kpi_card("Total Violations", str(total), "info"))
        parts.append(self._kpi_card("Correct", str(correct), "ok"))
        parts.append(self._kpi_card("Incorrect", str(incorrect), "bad"))
        parts.append("</div>")

        parts.append("<div class='section'>")
        parts.append("<h2>Threat Scores</h2>")
        if threat_scores:
            counts: Dict[int, int] = {}
            for k, v in threat_scores.items():
                try:
                    counts[int(k)] = int(v)
                except (TypeError, ValueError):
                    continue
            max_count = max(counts.values() or [0])
            total_count = sum(counts.values())
            parts.append("<div class='charts'>")
            parts.append("<div class='card chart-panel'>")
            parts.append("<div class='chart-label'>Threat Score</div>")
            parts.append("<div class='meta'>Number of functions per threat score (1 to 10)</div>")
            parts.append("<div class='chart-list'>")
            for score in range(1, 11):
                count = counts.get(score, 0)
                width = 0 if max_count == 0 else int((count / max_count) * 100)
                parts.append(
                    "<div class='chart'>"
                    f"<div><strong>{score}</strong></div>"
                    f"<div class='bar-wrap'><div class='bar' style='width:{width}%'></div></div>"
                    f"<div>{count}</div>"
                    "</div>"
                )
            parts.append("</div>")
            parts.append("</div>")

            stops = [
                (34, 197, 94),   # green
                (234, 179, 8),   # yellow
                (249, 115, 22),  # orange
                (220, 38, 38),   # red
            ]
            colors = []
            for i in range(1, 11):
                t = (i - 1) / 9
                if t <= 1 / 3:
                    t2 = t * 3
                    a, b = stops[0], stops[1]
                elif t <= 2 / 3:
                    t2 = (t - 1 / 3) * 3
                    a, b = stops[1], stops[2]
                else:
                    t2 = (t - 2 / 3) * 3
                    a, b = stops[2], stops[3]
                r = int(a[0] + (b[0] - a[0]) * t2)
                g = int(a[1] + (b[1] - a[1]) * t2)
                bch = int(a[2] + (b[2] - a[2]) * t2)
                colors.append(f"#{r:02x}{g:02x}{bch:02x}")
            if total_count > 0:
                start = 0.0
                segs = []
                for score in range(1, 11):
                    count = counts.get(score, 0)
                    if count <= 0:
                        continue
                    pct = (count / total_count) * 100
                    end = start + pct
                    color = colors[score - 1]
                    segs.append(f"{color} {start:.2f}% {end:.2f}%")
                    start = end
                gradient = ", ".join(segs) if segs else "#e5e7eb 0% 100%"
            else:
                gradient = "#e5e7eb 0% 100%"

            parts.append("<div class='card pie-card'>")
            parts.append("<div class='meta'>Distribution of functions by threat score</div>")
            parts.append("<div class='pie-row'>")
            parts.append("<div class='pie-wrap'>")
            parts.append("<div class='pie-tooltip' id='pie-tooltip'></div>")
            parts.append(self._pie_svg(counts, colors))
            parts.append("</div>")
            parts.append("<div class='pie-legend'>")
            for score in range(1, 11):
                color = colors[score - 1]
                count = counts.get(score, 0)
                parts.append(
                    f"<div><span class='swatch' style='background:{color}'></span>{score}: {count}</div>"
                )
            parts.append("</div>")
            parts.append("</div>")
            parts.append("</div>")
            parts.append("</div>")
        else:
            parts.append("<div class='card'><em>No threat scores found.</em></div>")
        parts.append("</div>")

        parts.append("<div class='section'>")
        parts.append("<h2>Submodules</h2>")
        if submodules:
            parts.append("<div class='card'>")
            parts.append("<ul>")
            for name in submodules:
                parts.append(f"<li>{html.escape(name)}</li>")
            parts.append("</ul>")
            parts.append("</div>")
        else:
            parts.append("<div class='card'><em>No submodules found.</em></div>")
        parts.append("</div>")

        parts.append("<div class='section'>")
        parts.append("<h2>Assessments</h2>")
        parts.append("<div class='card'><input id='search' type='search' placeholder='Filter by function, file, or text...'></div>")
        parts.append("<div id='assessments'>")
        idx = 1
        for score in [str(s) for s in range(1, 11)]:
            items = grouped.get(score, [])
            if not items:
                continue
            parts.append(
                f"<details class='score-group'><summary>Threat Score {score} — {len(items)} item(s)</summary>"
            )
            for item in items:
                parts.append(self._render_assessment(item, idx))
                idx += 1
            parts.append("</details>")
        unknown_items = grouped.get("Unknown", [])
        if unknown_items:
            parts.append(
                f"<details class='score-group'><summary>Incorrect Violations — {len(unknown_items)} item(s)</summary>"
            )
            for item in unknown_items:
                parts.append(self._render_assessment(item, idx))
                idx += 1
            parts.append("</details>")
        parts.append("</div>")
        parts.append("</div>")

        parts.append(
            "<script>"
            "const root=document.documentElement;"
            "const toggle=document.getElementById('theme-toggle');"
            "const setTheme=(t)=>{"
            "if(t==='light'){root.classList.add('theme-light');toggle.textContent='Dark mode';}"
            "else{root.classList.remove('theme-light');toggle.textContent='Light mode';}"
            "localStorage.setItem('theme',t);"
            "};"
            "const saved=localStorage.getItem('theme')||'light';"
            "setTheme(saved);"
            "toggle.addEventListener('click',()=>{"
            "const next=root.classList.contains('theme-light')?'dark':'light';"
            "setTheme(next);"
            "});"
            "const q=document.getElementById('search');"
            "const container=document.getElementById('assessments');"
            "q.addEventListener('input',()=>{"
            "const term=q.value.toLowerCase();"
            "if(!container) return;"
            "for(const group of container.querySelectorAll('.score-group')){"
            "let any=false;"
            "for(const el of group.querySelectorAll('details.assessment')){"
            "const text=el.getAttribute('data-search')||'';"
            "const show=text.includes(term);"
            "el.style.display=show?'block':'none';"
            "if(show) any=true;"
            "}"
            "group.style.display=any?'block':'none';"
            "}"
            "});"
            "const pie=document.querySelector('.pie');"
            "const tip=document.getElementById('pie-tooltip');"
            "if(pie && tip){"
            "pie.addEventListener('mousemove',(e)=>{"
            "const t=e.target;"
            "const label=t.getAttribute && t.getAttribute('data-label');"
            "if(!label){tip.style.opacity=0;return;}"
            "tip.textContent=label;"
            "const rect=pie.getBoundingClientRect();"
            "tip.style.left=(e.clientX-rect.left)+'px';"
            "tip.style.top=(e.clientY-rect.top)+'px';"
            "tip.style.opacity=1;"
            "});"
            "pie.addEventListener('mouseleave',()=>{tip.style.opacity=0;});"
            "}"
            "async function generateFix(btn) {"
            "  const funcName = new TextDecoder().decode(Uint8Array.from(atob(btn.dataset.func), c => c.charCodeAt(0)));"
            "  const precon = new TextDecoder().decode(Uint8Array.from(atob(btn.dataset.precon), c => c.charCodeAt(0)));"
            "  btn.disabled = true;"
            "  btn.textContent = 'Generating...';"
            "  const fixBox = btn.parentElement.querySelector('.fix-box');"
            "  const contentBox = fixBox.querySelector('.fix-content');"
            "  fixBox.classList.add('visible');"
            "  contentBox.innerHTML = '<em>Asking LLM to generate fix... this may take a few seconds.</em>';"
            "  try {"
            "    const res = await fetch('/api/suggest_fix', {"
            "      method: 'POST',"
            "      headers: { 'Content-Type': 'application/json' },"
            "      body: JSON.stringify({ target_func: funcName, target_precon: precon, project_dir: window.LAFVT_PROJECT_DIR, model: window.LAFVT_MODEL })"
            "    });"
            "    const data = await res.json();"
            "    if (!res.ok) throw new Error(data.error || 'Server error');"
            "    let html = '<strong>Fixable:</strong> ' + data.result.is_fixable + '<br><br>';"
            "    html += '<strong>Explanation:</strong><p>' + data.result.explanation + '</p>';"
            "    if (data.result.suggested_code_diff) {"
            "      html += '<strong>Suggested Diff:</strong><pre>' + data.result.suggested_code_diff + '</pre>';"
            "    }"
            "    if (data.result.extra_changes_required) {"
            "      html += '<strong>Extra Changes Required:</strong><p>' + data.result.extra_changes_required + '</p>';"
            "    }"
            "    contentBox.innerHTML = html;"
            "  } catch (err) {"
            "    contentBox.innerHTML = '<span style=\"color:var(--warn)\"><strong>Error:</strong> ' + err.message + '</span>';"
            "  } finally {"
            "    btn.disabled = false;"
            "    btn.textContent = 'Regenerate Fix';"
            "  }"
            "}"
            "async function stopServer() {"
            "  if (!confirm('Stop the server? You will need to restart it to use the report again.')) return;"
            "  try {"
            "    await fetch('/api/shutdown', { method: 'POST' });"
            "    document.body.innerHTML = '<div style=\"display:flex;align-items:center;justify-content:center;height:100vh;font-family:Georgia,serif;color:var(--ink)\">' +"
            "      '<div style=\"text-align:center\"><h1>Server Stopped</h1><p>You can close this tab.</p></div></div>';"
            "  } catch (err) {"
            "    document.body.innerHTML = '<div style=\"display:flex;align-items:center;justify-content:center;height:100vh;font-family:Georgia,serif\">' +"
            "      '<div style=\"text-align:center\"><h1>Server Stopped</h1><p>You can close this tab.</p></div></div>';"
            "  }"
            "}"
            "</script>"
        )
        parts.append("</body></html>")
        return "\n".join(parts)

    def _kpi_card(self, label: str, value: str, kind: str) -> str:
        return (
            f"<div class='card stat {html.escape(kind)}'>"
            f"<div class='stat-label'>{html.escape(label)}</div>"
            f"<div class='stat-value'>{html.escape(value)}</div>"
            "<div class='stat-bar'><span></span></div>"
            "</div>"
        )

    def _render_assessment(self, item: Dict[str, Any], idx: int) -> str:
        pre = html.escape(str(item.get("Precondition", "")))
        target = html.escape(str(item.get("Target Function", "")))
        source = html.escape(str(item.get("Source File", "")))
        assessment = item.get("Violation Assessment") or {}
        llm = item.get("LLM Review") or {}

        search_blob = " ".join(
            str(v) for v in [pre, target, source, assessment, llm] if v
        ).lower()
        parts: List[str] = []
        parts.append(f"<details class='assessment' data-search='{html.escape(search_blob)}'>")
        parts.append(
            "<summary>"
            f"#{idx} {target or '(unknown function)'}"
            "</summary>"
        )
        parts.append("<div class='meta'>")
        if source:
            parts.append(f"<div>Source file: {source}</div>")
        if pre:
            parts.append(f"<div>Precondition: <code>{pre}</code></div>")
        parts.append("</div>")

        parts.append("<div class='row'>")
        parts.append(self._render_violation_assessment(assessment))
        parts.append(self._render_llm_review(llm))
        parts.append("</div>")
        # Inject Generate Fix button and output container
        precondition = item.get("Precondition", "")
        
        # Use base64 encoding in data-attributes to avoid HTML entity escaping issues
        b64_target = base64.b64encode(item.get("Target Function", "").encode("utf-8")).decode("ascii")
        b64_precon = base64.b64encode(precondition.encode("utf-8")).decode("ascii")
        
        parts.append("<div style='margin-top: 16px'>")
        parts.append(f"<button class='btn-primary' data-func='{b64_target}' data-precon='{b64_precon}' onclick='generateFix(this)'>Generate Code Fix ✨</button>")
        parts.append(f"<div class='fix-box'><div class='meta'>AI Fix Suggestion</div><div class='fix-content'></div></div>")
        parts.append("</div>")

        parts.append("</details>")
        return "\n".join(parts)

    def _infer_codebase_name(self, assessments: List[Dict[str, Any]]) -> Optional[str]:
        for item in assessments:
            source = item.get("Source File")
            if not source:
                continue
            parts = Path(str(source)).parts
            for idx, seg in enumerate(parts):
                if seg.lower() in {"sys", "src"} and idx > 0:
                    return parts[idx - 1]
        return None

    def _collect_submodules(self, assessments: List[Dict[str, Any]], codebase: Optional[str]) -> List[str]:
        if not assessments:
            return []
        names = set()
        for item in assessments:
            source = item.get("Source File")
            if not source:
                continue
            parts = list(Path(str(source)).parts)
            if codebase:
                try:
                    base_idx = parts.index(codebase)
                    parts = parts[base_idx + 1 :]
                except ValueError:
                    pass
            sub = None
            if "sys" in parts:
                idx = parts.index("sys")
                if idx + 1 < len(parts):
                    sub = f"sys/{parts[idx+1]}"
            elif "cpu" in parts:
                idx = parts.index("cpu")
                if idx + 1 < len(parts):
                    sub = f"cpu/{parts[idx+1]}"
            elif "drivers" in parts:
                idx = parts.index("drivers")
                if idx + 1 < len(parts):
                    sub = f"drivers/{parts[idx+1]}"
            elif "boards" in parts:
                idx = parts.index("boards")
                if idx + 1 < len(parts):
                    sub = f"boards/{parts[idx+1]}"
            elif "harnesses" in parts:
                idx = parts.index("harnesses")
                if idx + 1 < len(parts):
                    sub = f"harnesses/{parts[idx+1]}"
            if sub:
                names.add(sub)
        return sorted(names)

    def _render_violation_assessment(self, assessment: Dict[str, Any]) -> str:
        items = []
        for key in ["Untrusted Input Source", "Reasoning", "Analysis"]:
            val = assessment.get(key)
            if val:
                items.append(f"<div><strong>{html.escape(key)}:</strong></div><pre>{html.escape(str(val))}</pre>")

        reviewer = assessment.get("Reviewer Agrees")
        rationale = assessment.get("Reviewer Rationle") or assessment.get("Reviewer Rationale")
        if reviewer is not None:
            items.append(
                f"<div><strong>Reviewer Agrees:</strong> {html.escape(str(reviewer))}</div>"
            )
        if rationale:
            items.append(f"<div><strong>Reviewer Rationale:</strong></div><pre>{html.escape(str(rationale))}</pre>")

        if not items:
            items.append("<em>No violation assessment details.</em>")

        return "<div class='box'><div class='meta'>Violation Assessment</div>" + "".join(items) + "</div>"

    def _render_llm_review(self, llm: Dict[str, Any]) -> str:
        items = []
        call_trace = llm.get("Call Trace")
        if call_trace:
            lines = "\n".join(str(x) for x in call_trace)
            items.append(f"<div><strong>Call Trace:</strong></div><pre>{html.escape(lines)}</pre>")
        origin = llm.get("Origin of Variable")
        if origin:
            items.append(f"<div><strong>Origin of Variable:</strong></div><pre>{html.escape(str(origin))}</pre>")
        threat = llm.get("Threat Assessment") or {}
        if threat:
            threat_text = "\n".join(f"{k}: {v}" for k, v in threat.items())
            items.append(f"<div><strong>Threat Assessment:</strong></div><pre>{html.escape(threat_text)}</pre>")
        threat_vector = llm.get("threat_vector")
        if threat_vector is None:
            threat_vector = llm.get("Threat Vector")
        if threat_vector:
            items.append(f"<div><strong>Threat Vector:</strong> {html.escape(str(threat_vector))}</div>")
        score = llm.get("Threat Score")
        if score is not None:
            items.append(f"<div><strong>Threat Score:</strong> {html.escape(str(score))}</div>")

        if not items:
            items.append("<em>No LLM review details.</em>")

        return "<div class='box'><div class='meta'>LLM Review</div>" + "".join(items) + "</div>"

    def _pie_svg(self, counts: Dict[int, int], colors: List[str]) -> str:
        import math

        def polar(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
            rad = math.radians(angle_deg)
            return (cx + radius * math.cos(rad), cy + radius * math.sin(rad))

        total = sum(counts.values())
        if total <= 0:
            return "<div class='pie' style='background:#e5e7eb'></div>"
        cx, cy, r = 100, 100, 90
        start_angle = -90.0
        parts: List[str] = []
        parts.append("<svg class='pie' viewBox='0 0 200 200' role='img' aria-label='Threat score distribution'>")
        for score in range(1, 11):
            count = counts.get(score, 0)
            if count <= 0:
                continue
            angle = (count / total) * 360.0
            end_angle = start_angle + angle
            x1, y1 = polar(cx, cy, r, start_angle)
            x2, y2 = polar(cx, cy, r, end_angle)
            large = 1 if angle > 180 else 0
            color = colors[score - 1]
            pct = (count / total) * 100.0
            label = html.escape(f"Score: {score} — {count} ({pct:.1f}%)")
            parts.append(
                f"<path d='M {cx} {cy} L {x1:.2f} {y1:.2f} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f} Z' "
                f"fill='{color}' stroke='#ffffff' stroke-width='0.5' data-label='{label}'></path>"
            )
            start_angle = end_angle
        parts.append("</svg>")
        return "".join(parts)

__all__ = ["ViolationAssessmentReport"]

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate violation assessment HTML report")
    parser.add_argument("--assessment", required=True, help="Path to violation_assessment.json")
    parser.add_argument("--report_name", default="output.html", help="Output HTML filename")
    parser.add_argument("--output_dir", help="Optional output directory for the HTML report")
    parser.add_argument("--project_dir", default="", help="Root directory of the project")
    parser.add_argument("--model", default="gpt-5.2", help="LLM model to use (default: gpt-5.2)")
    args = parser.parse_args()

    assessment_dir = Path(args.assessment)
    report_name = args.report_name
    if not report_name.lower().endswith(".html"):
        report_name = f"{report_name}.html"
    
    if args.output_dir:
        output_path = Path(args.output_dir) / report_name
    else:
        output_path = Path(report_name)
    var = ViolationAssessmentReport(assessment_dir, output_path, project_dir=args.project_dir, model=args.model)
    out_path = var.generate()
    print("Generated! ", out_path)


if __name__ == "__main__":
    main()
