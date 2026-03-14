"""AIda Web UI — Flask app with split chat/results layout."""

from __future__ import annotations

import json
import secrets
import sys
import os
from functools import wraps
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from aida.models import Project, Baseline, Selections
from aida.agents.intake import run_intake
from aida.agents.baseline import calculate_baseline
from aida.agents.alternatives import find_alternatives
from aida.agents.aggregate import compute_aggregate
from aida.agents.report import generate_report_markdown

app = Flask(__name__)
app.secret_key = os.environ.get('AIDA_SECRET_KEY', secrets.token_hex(32))

AIDA_PASSWORD = os.environ.get('AIDA_PASSWORD', '')


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not AIDA_PASSWORD:
            return f(*args, **kwargs)
        if session.get('authenticated'):
            return f(*args, **kwargs)
        if request.is_json:
            return jsonify({'error': 'Ej inloggad'}), 401
        return redirect(url_for('login'))
    return decorated


LOGIN_TEMPLATE = r"""<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AIda — Logga in</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root { --kk-gold: #FFCC01; --kk-dark-red: #B5201F; --kk-burgundy: #890200; --kk-charcoal: #444; --kk-cream: #FFF9DE; --kk-warm-bg: #FFFBF5; --kk-gray-200: #e5e5e5; --kk-gray-400: #a3a3a3; --kk-gold-light: #FFF1B6; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Roboto', sans-serif; height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--kk-warm-bg); }
.login-box { background: white; border-radius: 12px; padding: 40px; width: 360px; box-shadow: 0 4px 24px rgba(0,0,0,0.08); border-top: 3px solid var(--kk-gold-light); }
.login-box h1 { font-size: 24px; color: var(--kk-charcoal); margin-bottom: 8px; }
.login-box p { font-size: 13px; color: var(--kk-gray-400); margin-bottom: 24px; }
.login-box input { width: 100%; padding: 12px 16px; border: 1px solid var(--kk-gray-200); border-radius: 8px; font-size: 14px; font-family: inherit; outline: none; }
.login-box input:focus { border-color: var(--kk-dark-red); box-shadow: 0 0 0 2px rgba(181,32,31,0.15); }
.login-box button { width: 100%; padding: 12px; background: var(--kk-charcoal); color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; margin-top: 12px; font-family: inherit; }
.login-box button:hover { background: var(--kk-dark-red); }
.error { color: var(--kk-dark-red); font-size: 12px; margin-top: 8px; }
.footer { position: fixed; bottom: 16px; font-size: 11px; color: var(--kk-gray-400); }
</style>
</head>
<body>
<div class="login-box">
  <h1>AIda</h1>
  <p>Klimatkalkyl och beslutsstöd för ombyggnationer</p>
  <form method="POST">
    <input type="password" name="password" placeholder="Lösenord" autofocus>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <button type="submit">Logga in</button>
  </form>
</div>
<div class="footer"></div>
</body>
</html>"""


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not AIDA_PASSWORD:
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        if request.form.get('password') == AIDA_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('index'))
        error = 'Fel lösenord'
    return render_template_string(LOGIN_TEMPLATE, error=error)


@app.route('/')
@require_auth
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/intake', methods=['POST'])
@require_auth
def api_intake():
    data = request.json
    description = data.get('description', '')
    if not description:
        return jsonify({'error': 'Beskrivning saknas'}), 400

    try:
        result = run_intake(description)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/baseline', methods=['POST'])
@require_auth
def api_baseline():
    data = request.json
    project_data = data.get('project')

    if not project_data:
        return jsonify({'error': 'Projekt saknas'}), 400

    try:
        project = Project.from_dict(project_data)
        baseline = calculate_baseline(project)
        return jsonify(baseline.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alternatives', methods=['POST'])
@require_auth
def api_alternatives():
    data = request.json
    project_data = data.get('project')
    baseline_data = data.get('baseline')

    if not project_data or not baseline_data:
        return jsonify({'error': 'Projekt eller baslinje saknas'}), 400

    try:
        project = Project.from_dict(project_data)
        baseline = Baseline.from_dict(baseline_data)
        user_feedback = data.get('user_feedback')
        result = find_alternatives(project, baseline, user_feedback=user_feedback)
        return jsonify(result.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/aggregate', methods=['POST'])
@require_auth
def api_aggregate():
    data = request.json
    try:
        project = Project.from_dict(data.get('project', {}))
        selections = Selections.from_dict(data.get('selections', {}))
        result = compute_aggregate(project, selections)
        return jsonify(result.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/report', methods=['POST'])
@require_auth
def api_report():
    data = request.json
    try:
        project = Project.from_dict(data.get('project', {}))
        selections = Selections.from_dict(data.get('selections', {}))
        if not selections.components:
            return jsonify({'error': 'Inga komponenter valda'}), 400
        markdown = generate_report_markdown(project, selections)
        return jsonify({'markdown': markdown})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AIda — Klimatkalkyl för ombyggnationer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
/* === Karlstads kommun färgpalett (karlstad.se-manér) === */
:root {
  --kk-gold: #FFCC01;
  --kk-gold-light: #FFF1B6;
  --kk-orange: #EF7D00;
  --kk-red-orange: #E84E0D;
  --kk-red: #D41318;
  --kk-dark-red: #B5201F;
  --kk-burgundy: #890200;
  --kk-cream: #FFF9DE;
  --kk-warm-bg: #FFFBF5;
  --kk-charcoal: #444444;
  --kk-text: #444444;
  --kk-gray-50: #fafafa;
  --kk-gray-100: #f5f5f5;
  --kk-gray-200: #e5e5e5;
  --kk-gray-300: #d4d4d4;
  --kk-gray-400: #a3a3a3;
  --kk-gray-500: #737373;
  --green-saving: #4a7c59;
}

* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Roboto', -apple-system, BlinkMacSystemFont, sans-serif; height: 100vh; display: flex; flex-direction: column; background: white; color: var(--kk-text); }

/* === Top bar (karlstad.se: white with warm accent line) === */
.topbar { background: white; color: var(--kk-charcoal); height: 56px; display: flex; align-items: center; justify-content: space-between; padding: 0 24px; flex-shrink: 0; border-bottom: 3px solid var(--kk-gold-light); }
.topbar-logo { display: flex; align-items: center; gap: 10px; }
.topbar-logo svg { width: 28px; height: 28px; color: var(--kk-red-orange); }
.topbar-logo span { font-size: 16px; font-weight: 700; letter-spacing: 0.5px; color: var(--kk-charcoal); }
.topbar-center { font-size: 14px; color: var(--kk-gray-500); }
.topbar-right { font-size: 12px; color: var(--kk-gray-400); }

/* === Progress tracker (mockup: numbered circles with line) === */
.progress-bar { padding: 24px 48px 16px; flex-shrink: 0; }
.progress-track { display: flex; justify-content: space-between; align-items: flex-start; position: relative; }
.progress-line { position: absolute; top: 16px; left: 48px; right: 48px; height: 2px; background: var(--kk-gray-200); }
.progress-fill { position: absolute; top: 0; left: 0; height: 100%; background: var(--kk-charcoal); transition: width 0.5s ease; }
.step-item { display: flex; flex-direction: column; align-items: center; z-index: 1; min-width: 80px; }
.step-circle { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600; transition: all 0.3s; background: white; color: var(--kk-gray-400); border: 2px solid var(--kk-gray-200); }
.step-circle.active { background: var(--kk-dark-red); color: white; border-color: var(--kk-dark-red); box-shadow: 0 2px 8px rgba(181,32,31,0.3); transform: scale(1.1); }
.step-circle.done { background: var(--kk-charcoal); color: white; border-color: var(--kk-charcoal); }
.step-label { margin-top: 6px; font-size: 11px; font-weight: 500; color: var(--kk-gray-500); text-align: center; }
.step-label.active { color: var(--kk-charcoal); font-weight: 700; }
.step-label.done { color: var(--kk-charcoal); }

/* === Main layout === */
.main { display: flex; flex: 1; overflow: hidden; padding: 0 24px 0 24px; gap: 24px; }

/* === Chat panel (mockup: rounded, warm bg) === */
.chat-panel { width: 40%; display: flex; flex-direction: column; flex-shrink: 0; }
.chat-container { flex: 1; display: flex; flex-direction: column; background: var(--kk-warm-bg); border-radius: 12px; border: 1px solid var(--kk-gray-200); overflow: hidden; min-height: 0; }
.chat-header { padding: 10px 16px; border-bottom: 1px solid var(--kk-gray-200); background: var(--kk-cream); display: flex; justify-content: space-between; align-items: center; }
.chat-header h2 { font-size: 15px; font-weight: 600; color: var(--kk-charcoal); }
.messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.msg { padding: 10px 14px; border-radius: 16px; max-width: 85%; font-size: 13px; line-height: 1.5; }
.msg.user { background: #FFF0D4; color: var(--kk-text); align-self: flex-end; border-bottom-right-radius: 4px; }
.msg.bot { background: white; color: var(--kk-text); align-self: flex-start; border-bottom-left-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
@keyframes msgIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.msg { animation: msgIn 0.25s ease-out; }
.msg.system { background: var(--kk-cream); font-size: 12px; text-align: center; align-self: center; max-width: 100%; color: var(--kk-gray-500); border: 1px solid var(--kk-gray-200); }
.msg p { margin: 0 0 8px; }
.msg p:last-child { margin-bottom: 0; }
.msg ol, .msg ul { margin: 6px 0; padding-left: 20px; }
.msg li { margin-bottom: 4px; }
.msg h1, .msg h2, .msg h3, .msg h4 { margin: 8px 0 4px; line-height: 1.3; }
.msg h1 { font-size: 16px; } .msg h2 { font-size: 15px; } .msg h3 { font-size: 14px; } .msg h4 { font-size: 13px; }
.msg code { background: rgba(0,0,0,0.06); padding: 1px 4px; border-radius: 3px; font-size: 12px; }
.msg pre { background: rgba(0,0,0,0.06); padding: 8px 10px; border-radius: 6px; overflow-x: auto; margin: 6px 0; }
.msg pre code { background: none; padding: 0; }
.msg table { border-collapse: collapse; width: 100%; margin: 6px 0; font-size: 12px; }
.msg table th, .msg table td { padding: 4px 8px; border: 1px solid var(--kk-gray-200); text-align: left; }
.msg table th { background: var(--kk-gray-50); font-weight: 600; }
.msg blockquote { border-left: 3px solid var(--kk-gray-300); margin: 6px 0; padding: 2px 10px; color: var(--kk-gray-500); }
.chat-input { padding: 12px 16px; border-top: 1px solid var(--kk-gray-200); background: var(--kk-cream); display: flex; align-items: center; gap: 8px; }
.chat-input input { flex: 1; padding: 10px 16px; border: 1px solid var(--kk-gray-200); border-radius: 24px; font-size: 13px; font-family: inherit; background: white; outline: none; }
.chat-input input:focus { border-color: var(--kk-dark-red); box-shadow: 0 0 0 2px rgba(181,32,31,0.15); }
.chat-input button { width: 40px; height: 40px; border-radius: 50%; background: var(--kk-charcoal); color: white; border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background 0.2s; flex-shrink: 0; }
.chat-input button:hover:not(:disabled) { background: var(--kk-dark-red); }
.chat-input button:disabled { opacity: 0.4; cursor: not-allowed; }
.chat-disclaimer { text-align: center; font-size: 11px; color: var(--kk-gray-400); padding: 6px 0 12px; }

/* === Results panel (mockup: tabs + white bg) === */
.results-panel { width: 60%; display: flex; flex-direction: column; overflow: hidden; min-height: 0; }
.results-content { flex: 1; overflow-y: auto; padding: 20px 8px; background: var(--kk-gray-50); border-radius: 0 0 8px 8px; }

/* === Component cards (mockup style) === */
.comp-card { background: white; border: 1px solid var(--kk-gray-200); border-radius: 8px; overflow: hidden; margin-bottom: 16px; }
.comp-card-header { padding: 12px 16px; background: var(--kk-gray-50); border-bottom: 1px solid var(--kk-gray-200); }
.comp-card-header h3 { font-size: 14px; font-weight: 600; color: var(--kk-charcoal); }
.comp-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.comp-table th { padding: 8px 12px; text-align: left; font-weight: 500; color: var(--kk-gray-500); font-size: 12px; border-bottom: 1px solid var(--kk-gray-200); }
.comp-table td { padding: 10px 12px; border-bottom: 1px solid var(--kk-gray-100); }
.comp-table tr:last-child td { border-bottom: none; }
.alt-row { cursor: pointer; transition: background 0.15s; }
.alt-row:hover { background: var(--kk-gray-50); }
.alt-row.selected { background: var(--kk-gold-light) !important; }
.alt-row input[type=radio] { accent-color: var(--kk-charcoal); }
.type-badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.type-baseline { background: var(--kk-gray-100); color: var(--kk-charcoal); }
.type-reuse { background: var(--kk-gold-light); color: #7A6000; }
.type-optimized { background: #FDE8D0; color: var(--kk-red-orange); }

/* === Summary cards === */
.summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }
.card { background: var(--kk-gray-50); border: 1px solid var(--kk-gray-200); border-radius: 8px; padding: 16px; }
.card .card-title { font-size: 11px; font-weight: 600; color: var(--kk-gray-500); text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { font-size: 24px; font-weight: 700; color: var(--kk-charcoal); margin-top: 4px; }
.card .sublabel { font-size: 12px; color: var(--kk-gray-500); }
.card.saving .value { color: var(--green-saving); }

/* === Source badges === */
.source-badge { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; margin-right: 3px; }
.source-verified { background: #F0E0E0; color: var(--kk-burgundy); }
.source-estimate { background: var(--kk-gold-light); color: #8B6914; }
.source-legend { display: flex; gap: 16px; margin: 4px 0 12px; font-size: 12px; color: var(--kk-gray-500); }

/* === Buttons === */
.btn { padding: 10px 20px; background: var(--kk-dark-red); color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; font-family: inherit; margin-top: 12px; transition: background 0.2s; }
.btn:hover { background: var(--kk-burgundy); }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-secondary { background: var(--kk-gray-100); color: var(--kk-charcoal); }
.btn-secondary:hover { background: var(--kk-gray-200); }

.section-title { font-size: 15px; font-weight: 600; margin: 16px 0 6px; color: var(--kk-charcoal); }
.report-area { background: white; border: 1px solid var(--kk-gray-200); border-radius: 8px; padding: 20px; margin-top: 16px; font-size: 13px; line-height: 1.6; max-height: 500px; overflow-y: auto; }
.report-area h1 { font-size: 20px; font-weight: 700; margin: 0 0 12px; color: var(--kk-charcoal); border-bottom: 2px solid var(--kk-gray-200); padding-bottom: 6px; }
.report-area h2 { font-size: 16px; font-weight: 600; margin: 16px 0 8px; color: var(--kk-charcoal); }
.report-area h3 { font-size: 14px; font-weight: 600; margin: 12px 0 6px; color: var(--kk-charcoal); }
.report-area p { margin: 0 0 10px; }
.report-area ul, .report-area ol { margin: 6px 0 10px; padding-left: 24px; }
.report-area li { margin-bottom: 4px; }
.report-area table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 12px; }
.report-area table th, .report-area table td { padding: 6px 10px; border: 1px solid var(--kk-gray-200); text-align: left; }
.report-area table th { background: var(--kk-gray-50); font-weight: 600; font-size: 11px; color: var(--kk-gray-500); }
.report-area strong { font-weight: 600; }
.report-area blockquote { border-left: 3px solid var(--kk-gold); margin: 8px 0; padding: 4px 12px; background: var(--kk-cream); color: var(--kk-gray-500); font-style: italic; }
.report-area hr { border: none; border-top: 1px solid var(--kk-gray-200); margin: 16px 0; }

/* === Footer (karlstad.se: warm cream) === */
.footer { background: var(--kk-cream); color: var(--kk-gray-500); height: 36px; display: flex; align-items: center; justify-content: center; font-size: 11px; flex-shrink: 0; border-top: 1px solid var(--kk-gray-200); }

/* === Scrollbar === */
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--kk-gray-300); border-radius: 8px; }
::-webkit-scrollbar-thumb:hover { background: var(--kk-gray-400); }
html { scrollbar-width: thin; scrollbar-color: #d4d4d4 transparent; }

.empty-state { color: var(--kk-gray-400); text-align: center; margin-top: 80px; }
.empty-state p { font-size: 14px; }

/* === Results tabs === */
.results-tabs { display: flex; border-bottom: 2px solid var(--kk-gray-200); flex-shrink: 0; background: white; border-radius: 8px 8px 0 0; }
.tab { padding: 10px 20px; background: none; border: none; font-size: 13px; font-weight: 500; color: var(--kk-gray-400); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; font-family: inherit; transition: all 0.2s; }
.tab:hover:not(:disabled) { color: var(--kk-charcoal); }
.tab.active { color: var(--kk-charcoal); border-bottom-color: var(--kk-dark-red); font-weight: 600; }
.tab:disabled { opacity: 0.35; cursor: not-allowed; }

/* === Confirm actions in chat === */
.confirm-actions { display: flex; gap: 8px; margin-top: 10px; }
.btn-confirm { padding: 8px 20px; background: var(--kk-charcoal); color: white; border: none; border-radius: 20px; font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit; transition: background 0.2s; }
.btn-confirm:hover { background: var(--kk-dark-red); }
.confirm-hint { font-size: 11px; color: var(--kk-gray-400); margin-top: 6px; }
</style>
</head>
<body>

<!-- Top bar -->
<div class="topbar">
  <div class="topbar-logo">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v3M12 20v3M1 12h3M20 12h3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></svg>
    <span>AIda</span>
  </div>
  <div class="topbar-center">Klimatkalkyl och beslutsstöd for ombyggnationer</div>
  <div class="topbar-right">Tidig prototyp under arbete</div>
</div>

<!-- Progress tracker -->
<div class="progress-bar">
  <div class="progress-track">
    <div class="progress-line"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
    <div class="step-item" data-step="intake">
      <div class="step-circle" id="sc-intake">1</div>
      <div class="step-label" id="sl-intake">Beskrivning</div>
    </div>
    <div class="step-item" data-step="baseline">
      <div class="step-circle" id="sc-baseline">2</div>
      <div class="step-label" id="sl-baseline">Baslinje</div>
    </div>
    <div class="step-item" data-step="alternatives">
      <div class="step-circle" id="sc-alternatives">3</div>
      <div class="step-label" id="sl-alternatives">Alternativ</div>
    </div>
    <div class="step-item" data-step="select">
      <div class="step-circle" id="sc-select">4</div>
      <div class="step-label" id="sl-select">Valda alternativ</div>
    </div>
    <div class="step-item" data-step="report">
      <div class="step-circle" id="sc-report">5</div>
      <div class="step-label" id="sl-report">Rapport</div>
    </div>
  </div>
</div>

<!-- Main content -->
<div class="main">
  <!-- Chat panel -->
  <div class="chat-panel">
    <div class="chat-container">
      <div class="chat-header">
        <h2>AIda</h2>
      </div>
      <div class="messages" id="messages">
        <div class="msg bot">Hej! Beskriv ditt ombyggnadsprojekt. Ange byggnadstyp, ungefärlig yta och vilka åtgärder som ska göras.</div>
      </div>
      <div class="chat-input">
        <input id="userInput" type="text" placeholder="Skriv ditt meddelande..." onkeydown="if(event.key==='Enter')sendMessage()">
        <button id="sendBtn" onclick="sendMessage()" aria-label="Skicka">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </button>
      </div>
    </div>
    <div class="chat-disclaimer">AIda kan göra misstag. Kontrollera viktig information.</div>
  </div>

  <!-- Results panel -->
  <div class="results-panel" id="results">
    <div class="results-tabs" id="resultTabs" style="display:none">
      <button class="tab" id="tab-projekt" onclick="switchTab('projekt')" disabled>Projekt</button>
      <button class="tab" id="tab-baslinje" onclick="switchTab('baslinje')" disabled>Baslinje</button>
      <button class="tab" id="tab-alternativ" onclick="switchTab('alternativ')" disabled>Alternativ</button>
      <button class="tab" id="tab-rapport" onclick="switchTab('rapport')" disabled>Rapport</button>
    </div>
    <div class="results-content" id="resultContent">
      <div class="empty-state">
        <p>Beskriv ditt projekt i chatten till vänster för att börja.</p>
      </div>
    </div>
  </div>
</div>

<!-- Footer -->
<div class="footer"></div>

<script>
// Configure marked
if (typeof marked !== 'undefined') {
  marked.setOptions({ breaks: true, gfm: true });
}

let state = {
  project: null, baseline: null, alternatives: null,
  selections: {}, pendingDesc: null, reportMarkdown: null,
  step: 'idle' // idle, intake_done, baseline_done, alternatives_done, report_done
};
let activeTab = null;

function renderMd(text) {
  text = text.replace(/(\d+)\)\s/g, (match, num, offset) => {
    return '\n' + num + '. ';
  }).trim();
  if (typeof marked !== 'undefined') return marked.parse(text);
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>');
}

function addMsg(text, cls) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  if (cls === 'bot' || cls === 'system') { d.innerHTML = renderMd(text); }
  else { d.textContent = text; }
  document.getElementById('messages').appendChild(d);
  d.scrollIntoView({behavior:'smooth'});
}

function addConfirmMsg(text, btnLabel, hint) {
  const d = document.createElement('div');
  d.className = 'msg bot';
  d.innerHTML = renderMd(text) +
    '<div class="confirm-actions"><button class="btn-confirm" onclick="confirmStep()">' + btnLabel + '</button></div>' +
    '<div class="confirm-hint">' + hint + '</div>';
  document.getElementById('messages').appendChild(d);
  d.scrollIntoView({behavior:'smooth'});
}

function removeConfirmButtons() {
  document.querySelectorAll('.confirm-actions').forEach(el => {
    const msg = el.closest('.msg');
    if (msg) { el.remove(); const hint = msg.querySelector('.confirm-hint'); if (hint) hint.remove(); }
  });
}

function setProgressStep(name) {
  const order = ['intake','baseline','alternatives','select','report'];
  const ni = order.indexOf(name);
  const pct = order.length > 1 ? (ni / (order.length - 1)) * 100 : 0;
  document.getElementById('progressFill').style.width = pct + '%';
  order.forEach((s, i) => {
    const circle = document.getElementById('sc-' + s);
    const label = document.getElementById('sl-' + s);
    circle.className = 'step-circle' + (i < ni ? ' done' : i === ni ? ' active' : '');
    label.className = 'step-label' + (i < ni ? ' done' : i === ni ? ' active' : '');
    if (i < ni) circle.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    else circle.textContent = i + 1;
  });
}

function setLoading(on) {
  document.getElementById('sendBtn').disabled = on;
  document.getElementById('userInput').disabled = on;
}

// === Tab system ===
function enableTab(name) {
  const tab = document.getElementById('tab-' + name);
  if (tab) tab.disabled = false;
  document.getElementById('resultTabs').style.display = 'flex';
}

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll('.results-tabs .tab').forEach(t => t.classList.remove('active'));
  const tab = document.getElementById('tab-' + name);
  if (tab) tab.classList.add('active');
  // Render from state
  if (name === 'projekt' && state.project) renderProjektContent();
  else if (name === 'baslinje' && state.baseline) renderBaslinjeContent();
  else if (name === 'alternativ' && state.alternatives) renderAlternativContent();
  else if (name === 'rapport' && state.reportMarkdown) renderRapportContent();
}

// === Chat input ===
async function sendMessage() {
  const input = document.getElementById('userInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addMsg(text, 'user');
  setLoading(true);
  removeConfirmButtons();

  switch (state.step) {
    case 'idle':
      if (state.pendingDesc) {
        await runIntake(state.pendingDesc + '\n\nFörtydligande: ' + text);
      } else {
        await runIntake(text);
      }
      break;
    case 'intake_done':
      addMsg('Uppdaterar projektbeskrivning...', 'system');
      await runIntake(state.project.description + '\n\nKorrigering: ' + text);
      break;
    case 'baseline_done':
      addMsg('Uppdaterar projektet...', 'system');
      await runIntake(state.project.description + '\n\nKorrigering: ' + text);
      break;
    case 'alternatives_done':
      addMsg('Söker fler alternativ...', 'system');
      await runAlternatives(text);
      break;
    case 'report_done':
      addMsg('Rapporten är klar. Ladda om sidan för att starta ett nytt projekt.', 'bot');
      setLoading(false);
      break;
    default:
      setLoading(false);
  }
}

// === Confirm step ===
function confirmStep() {
  removeConfirmButtons();
  if (state.step === 'intake_done') runBaseline();
  else if (state.step === 'baseline_done') runAlternatives();
}

// === Pipeline: Intake ===
async function runIntake(desc) {
  addMsg('Analyserar projektbeskrivning...', 'system');
  setProgressStep('intake');
  try {
    const r = await fetch('/api/intake', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({description: desc})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }

    if (d.clarification_needed) {
      state.pendingDesc = desc;
      state.project = null;
      state.step = 'idle';
      if (d.components && d.components.length) {
        const list = d.components.map(c => '- ' + c.name).join('\n');
        addMsg('Hittade hittills:\n' + list, 'bot');
      }
      addMsg(d.clarification_needed, 'bot');
      setLoading(false);
      return;
    }

    state.pendingDesc = null;
    state.project = d;
    state.baseline = null;
    state.alternatives = null;
    state.selections = {};
    state.reportMarkdown = null;
    state.step = 'intake_done';

    enableTab('projekt');
    switchTab('projekt');
    // Disable later tabs if re-running
    ['baslinje','alternativ','rapport'].forEach(t => { const el = document.getElementById('tab-'+t); if(el) el.disabled = true; });

    const compList = d.components.map(c => '- ' + c.name + ' (' + c.quantity + ' ' + c.unit + ')').join('\n');
    addConfirmMsg(
      '**' + d.building_type + '**, ' + d.area_bta + ' m\u00b2\n\n**Komponenter:**\n' + compList,
      'Bekr\u00e4fta och ber\u00e4kna baslinje \u2192',
      'Skriv i chatten om n\u00e5got inte st\u00e4mmer.'
    );
    setLoading(false);
  } catch(e) { addMsg('Fel: ' + e.message, 'system'); setLoading(false); }
}

// === Pipeline: Baseline ===
async function runBaseline() {
  addMsg('Ber\u00e4knar baslinje (NollCO2)...', 'system');
  setProgressStep('baseline');
  setLoading(true);
  try {
    const r = await fetch('/api/baseline', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({project: state.project})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.baseline = d;
    state.alternatives = null;
    state.selections = {};
    state.reportMarkdown = null;
    state.step = 'baseline_done';

    enableTab('baslinje');
    switchTab('baslinje');
    ['alternativ','rapport'].forEach(t => { const el = document.getElementById('tab-'+t); if(el) el.disabled = true; });

    const total = d.components.reduce((s,c) => s + c.co2e_kg, 0);
    addConfirmMsg(
      'Baslinje klar: **' + Math.round(total).toLocaleString('sv') + ' kg CO\u2082e** totalt f\u00f6r ' + d.components.length + ' komponenter.',
      'Bekr\u00e4fta och s\u00f6k alternativ \u2192',
      'Skriv i chatten om du vill korrigera n\u00e5got.'
    );
    setLoading(false);
  } catch(e) { addMsg('Fel: ' + e.message, 'system'); setLoading(false); }
}

// === Pipeline: Alternatives ===
async function runAlternatives(userFeedback) {
  addMsg('S\u00f6ker klimatsmarta alternativ...', 'system');
  setProgressStep('alternatives');
  setLoading(true);
  try {
    const body = {project: state.project, baseline: state.baseline};
    if (userFeedback) body.user_feedback = userFeedback;
    const r = await fetch('/api/alternatives', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.alternatives = d;
    state.selections = {};
    state.reportMarkdown = null;
    state.step = 'alternatives_done';
    setProgressStep('select');

    enableTab('alternativ');
    switchTab('alternativ');
    document.getElementById('tab-rapport').disabled = true;

    addMsg('Alternativ klara! V\u00e4lj per komponent i resultatpanelen.\n\nSkriv i chatten om du vill ha fler alternativ, t.ex. *"fler materialval f\u00f6r v\u00e4ggar"*.', 'bot');
    setLoading(false);
  } catch(e) { addMsg('Fel: ' + e.message, 'system'); setLoading(false); }
}

// === Pipeline: Report ===
async function generateReport() {
  setProgressStep('report');
  addMsg('Genererar rapport...', 'system');
  document.getElementById('reportBtn').disabled = true;
  setLoading(true);
  try {
    const sels = {components: Object.values(state.selections)};
    const r = await fetch('/api/report', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({project: state.project, selections: sels})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.reportMarkdown = d.markdown;
    state.step = 'report_done';
    addMsg('Rapport klar!', 'bot');
    enableTab('rapport');
    switchTab('rapport');
    setLoading(false);
  } catch(e) { addMsg('Fel: ' + e.message, 'system'); setLoading(false); }
}

// === Helpers ===
function formatSource(source) {
  if (!source) return '';
  if (source.startsWith('[Verifierad]')) return '<span class="source-badge source-verified">EPD</span>' + source.replace('[Verifierad] ', '');
  if (source.startsWith('[Uppskattning]')) return '<span class="source-badge source-estimate">Est.</span>' + source.replace('[Uppskattning] ', '');
  return source;
}

function getTypeBadge(alt) {
  if (alt.alternative_type === 'reuse') return '<span class="type-badge type-reuse">\u00c5terbruk</span>';
  if (alt.alternative_type === 'climate_optimized') return '<span class="type-badge type-optimized">Klimatopt.</span>';
  return '<span class="type-badge type-baseline">Baslinje</span>';
}

// === Tab renderers ===
function renderProjektContent() {
  const d = state.project;
  let html = '<div class="section-title">Projektinformation</div>';
  html += '<div class="comp-card"><div class="comp-card-header"><h3>' + d.building_type + ' \u2014 ' + d.area_bta + ' m\u00b2 BTA' + (d.name ? ' (' + d.name + ')' : '') + '</h3></div>';
  html += '<table class="comp-table"><thead><tr><th>Komponent</th><th>Antal</th><th>Enhet</th><th>Kategori</th></tr></thead><tbody>';
  d.components.forEach(c => {
    html += '<tr><td style="font-weight:500">' + c.name + '</td><td>' + c.quantity + '</td><td>' + c.unit + '</td><td>' + (c.category || '\u2013') + '</td></tr>';
  });
  html += '</tbody></table></div>';
  if (d.description) {
    html += '<div class="comp-card" style="margin-top:12px"><div class="comp-card-header"><h3>Beskrivning</h3></div><div style="padding:12px 16px;font-size:13px;color:var(--kk-gray-500);line-height:1.5">' + d.description + '</div></div>';
  }
  document.getElementById('resultContent').innerHTML = html;
}

function renderBaslinjeContent() {
  const d = state.baseline;
  const total = d.components.reduce((s,c) => s + c.co2e_kg, 0);
  const totalCost = d.components.reduce((s,c) => s + c.cost_sek, 0);
  let html = '<div class="section-title">Baslinje (NollCO2-metoden)</div>';
  html += '<div class="source-legend"><span><span class="source-badge source-verified">EPD</span> Verifierad k\u00e4lla</span><span><span class="source-badge source-estimate">Est.</span> Uppskattning</span></div>';
  html += '<div class="summary">';
  html += '<div class="card"><div class="card-title">Total CO\u2082e</div><div class="value">' + Math.round(total).toLocaleString('sv') + '</div><div class="sublabel">kg CO\u2082e</div></div>';
  html += '<div class="card"><div class="card-title">Total kostnad</div><div class="value">' + Math.round(totalCost).toLocaleString('sv') + '</div><div class="sublabel">SEK</div></div>';
  html += '<div class="card"><div class="card-title">Komponenter</div><div class="value">' + d.components.length + '</div><div class="sublabel">st</div></div>';
  html += '</div>';
  html += '<div class="comp-card"><div class="comp-card-header"><h3>Per komponent</h3></div>';
  html += '<table class="comp-table"><thead><tr><th>Komponent</th><th style="text-align:right">CO\u2082e (kg)</th><th style="text-align:right">Kostnad (SEK)</th><th>K\u00e4lla</th></tr></thead><tbody>';
  d.components.forEach(c => {
    html += '<tr><td style="font-weight:500">' + c.component_name + '</td><td style="text-align:right">' + Math.round(c.co2e_kg).toLocaleString('sv') + '</td><td style="text-align:right">' + Math.round(c.cost_sek).toLocaleString('sv') + '</td><td style="font-size:11px">' + formatSource(c.source) + '</td></tr>';
  });
  html += '</tbody></table></div>';
  document.getElementById('resultContent').innerHTML = html;
}

function renderAlternativContent() {
  const data = state.alternatives;
  let html = '<div class="section-title">J\u00e4mf\u00f6relse per komponent</div>';
  html += '<div class="source-legend"><span><span class="source-badge source-verified">EPD</span> Verifierad k\u00e4lla</span><span><span class="source-badge source-estimate">Est.</span> Uppskattning</span></div>';
  data.components.forEach(comp => {
    html += '<div class="comp-card"><div class="comp-card-header"><h3>' + comp.component_name + '</h3></div>';
    html += '<table class="comp-table"><thead><tr><th style="width:32px"></th><th>Typ</th><th>Material</th><th>K\u00e4lla</th><th style="text-align:right">CO\u2082e (kg)</th><th style="text-align:right">Kostnad</th></tr></thead><tbody>';
    const blSel = state.selections[comp.component_id] && state.selections[comp.component_id].selected_alternative.name === 'Baslinje';
    html += '<tr class="alt-row' + (blSel ? ' selected' : '') + '" data-comp="' + comp.component_id + '" data-alt="baseline">' +
      '<td><input type="radio" name="' + comp.component_id + '"' + (blSel ? ' checked' : '') + '></td>' +
      '<td><span class="type-badge type-baseline">Baslinje</span></td>' +
      '<td style="font-weight:500">Konventionellt</td><td style="font-size:11px">NollCO2</td>' +
      '<td style="text-align:right">' + Math.round(comp.baseline_co2e_kg) + '</td>' +
      '<td style="text-align:right">' + Math.round(comp.baseline_cost_sek).toLocaleString('sv') + ' kr</td></tr>';
    comp.alternatives.forEach((alt, i) => {
      const saving = Math.round((1 - alt.co2e_kg / comp.baseline_co2e_kg) * 100);
      const isSel = state.selections[comp.component_id] && state.selections[comp.component_id].selected_alternative.name === alt.name;
      html += '<tr class="alt-row' + (isSel ? ' selected' : '') + '" data-comp="' + comp.component_id + '" data-alt="' + i + '">' +
        '<td><input type="radio" name="' + comp.component_id + '"' + (isSel ? ' checked' : '') + '></td>' +
        '<td>' + getTypeBadge(alt) + '</td>' +
        '<td style="font-weight:500">' + alt.name + '</td>' +
        '<td style="font-size:11px">' + formatSource(alt.source) + '</td>' +
        '<td style="text-align:right">' + Math.round(alt.co2e_kg) + ' <span style="color:var(--green-saving);font-size:11px">\u2193' + saving + '%</span></td>' +
        '<td style="text-align:right">' + Math.round(alt.cost_sek).toLocaleString('sv') + ' kr</td></tr>';
    });
    html += '</tbody></table></div>';
  });
  html += '<div id="summaryArea"></div>';
  html += '<button class="btn" id="reportBtn" onclick="generateReport()" disabled>Generera rapport</button>';
  document.getElementById('resultContent').innerHTML = html;
  // Bind click handlers
  document.querySelectorAll('.alt-row').forEach(row => {
    row.onclick = function() { selectAlt(this.dataset.comp, this.dataset.alt, this); };
  });
  if (Object.keys(state.selections).length > 0) updateSummary();
}

function renderRapportContent() {
  let html = '<div class="report-area">' + renderMd(state.reportMarkdown) + '</div>';
  html += '<button class="btn btn-secondary" id="dlBtn" style="margin-top:12px"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>Ladda ner (.md)</button>';
  document.getElementById('resultContent').innerHTML = html;
  document.getElementById('dlBtn').onclick = () => {
    const blob = new Blob([state.reportMarkdown], {type:'text/markdown'});
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'aida-rapport.md'; a.click();
  };
}

// === Selection handling ===
function selectAlt(compId, altIdx, row) {
  row.closest('table').querySelectorAll('.alt-row').forEach(r => r.classList.remove('selected'));
  row.classList.add('selected');
  row.querySelector('input[type=radio]').checked = true;
  const comp = state.alternatives.components.find(c => c.component_id === compId);
  if (altIdx === 'baseline') {
    state.selections[compId] = { id: compId, name: comp.component_name,
      selected_alternative: {name:'Baslinje', co2e_kg: comp.baseline_co2e_kg, cost_sek: comp.baseline_cost_sek, source:'NollCO2'},
      baseline_co2e_kg: comp.baseline_co2e_kg, baseline_cost_sek: comp.baseline_cost_sek };
  } else {
    const alt = comp.alternatives[parseInt(altIdx)];
    state.selections[compId] = { id: compId, name: comp.component_name,
      selected_alternative: {name: alt.name, co2e_kg: alt.co2e_kg, cost_sek: alt.cost_sek, source: alt.source},
      baseline_co2e_kg: comp.baseline_co2e_kg, baseline_cost_sek: comp.baseline_cost_sek };
  }
  updateSummary();
}

function updateSummary() {
  const sels = Object.values(state.selections);
  const totalCo2 = sels.reduce((s,c) => s + c.selected_alternative.co2e_kg, 0);
  const totalCost = sels.reduce((s,c) => s + c.selected_alternative.cost_sek, 0);
  const blCo2 = sels.reduce((s,c) => s + c.baseline_co2e_kg, 0);
  const saving = blCo2 - totalCo2;
  const pct = blCo2 > 0 ? Math.round(saving / blCo2 * 100) : 0;
  document.getElementById('summaryArea').innerHTML =
    '<div class="summary">' +
    '<div class="card"><div class="card-title">Baslinje</div><div class="value">' + Math.round(blCo2).toLocaleString('sv') + '</div><div class="sublabel">kg CO\u2082e</div></div>' +
    '<div class="card saving"><div class="card-title">Besparing</div><div class="value">\u2193 ' + Math.round(saving).toLocaleString('sv') + '</div><div class="sublabel">kg CO\u2082e (' + pct + '%)</div></div>' +
    '<div class="card"><div class="card-title">Valda alternativ</div><div class="value">' + Math.round(totalCost).toLocaleString('sv') + '</div><div class="sublabel">SEK total kostnad</div></div>' +
    '</div>';
  const allSelected = state.alternatives.components.every(c => state.selections[c.component_id]);
  document.getElementById('reportBtn').disabled = !allSelected;
}
</script>
</body>
</html>
"""


def main():
    import argparse
    parser = argparse.ArgumentParser(description='AIda Web UI')
    parser.add_argument('--port', type=int, default=5002)
    parser.add_argument('--host', type=str, default='0.0.0.0')
    args = parser.parse_args()

    print(f"AIda web UI: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
