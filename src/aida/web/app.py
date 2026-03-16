"""AIda Web UI. Flask app with split chat/results layout."""

from __future__ import annotations

import json
import os
import secrets
import sys
from functools import wraps
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from aida.agents.aggregate import compute_aggregate
from aida.agents.alternatives import find_alternatives
from aida.agents.baseline import calculate_baseline
from aida.agents.intake import run_intake
from aida.agents.report import generate_report_markdown
from aida.models import Baseline, Project, Selections

app = Flask(__name__)
app.secret_key = os.environ.get('AIDA_SECRET_KEY', secrets.token_hex(32))

AIDA_PASSWORD = os.environ.get('AIDA_PASSWORD', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET', '')

try:
    import jwt as pyjwt
    from jwt import PyJWKClient
except ImportError:
    pyjwt = None
    PyJWKClient = None

# JWKS client for ES256 token verification (cached, Supabase default since 2026)
_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None and PyJWKClient and SUPABASE_URL:
        _jwks_client = PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")
    return _jwks_client


def get_user_from_token():
    """Extract user_id from Supabase JWT in Authorization header."""
    if not pyjwt or not SUPABASE_URL:
        return None
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header[7:]
    try:
        jwks = _get_jwks_client()
        if jwks:
            signing_key = jwks.get_signing_key_from_jwt(token)
            payload = pyjwt.decode(
                token, signing_key.key,
                algorithms=['ES256'], audience='authenticated'
            )
        elif SUPABASE_JWT_SECRET:
            # Fallback for legacy HS256 projects
            payload = pyjwt.decode(
                token, SUPABASE_JWT_SECRET,
                algorithms=['HS256'], audience='authenticated'
            )
        else:
            return None
        return payload.get('sub')
    except Exception:
        return None


def supabase_request(method, path, data=None, token=None, params=None):
    """Make a request to Supabase REST API (PostgREST)."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += '?' + urlencode(params)
    headers = {
        'apikey': SUPABASE_ANON_KEY,
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            resp_data = resp.read().decode()
            return json.loads(resp_data) if resp_data else None
    except HTTPError as e:
        error_body = e.read().decode()
        raise Exception(f"Supabase error {e.code}: {error_body}")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Supabase JWT auth
        if SUPABASE_URL:
            user_id = get_user_from_token()
            if not user_id:
                return jsonify({'error': 'Ej inloggad'}), 401
            request.user_id = user_id
            return f(*args, **kwargs)
        # Legacy password auth
        if not AIDA_PASSWORD:
            return f(*args, **kwargs)
        if session.get('authenticated'):
            return f(*args, **kwargs)
        if request.is_json:
            return jsonify({'error': 'Ej inloggad'}), 401
        return redirect(url_for('login'))
    return decorated


def require_supabase_auth(f):
    """Like require_auth but only allows Supabase JWT (for CRUD endpoints)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not SUPABASE_URL:
            return jsonify({'error': 'Supabase ej konfigurerat'}), 501
        user_id = get_user_from_token()
        if not user_id:
            return jsonify({'error': 'Ej inloggad'}), 401
        request.user_id = user_id
        return f(*args, **kwargs)
    return decorated


LOGIN_TEMPLATE = r"""<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AIda | Logga in</title>
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
def index():
    if SUPABASE_URL:
        return render_template_string(HTML_TEMPLATE,
            supabase_url=SUPABASE_URL,
            supabase_anon_key=SUPABASE_ANON_KEY,
            has_supabase=True)
    if AIDA_PASSWORD and not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template_string(HTML_TEMPLATE,
        supabase_url='', supabase_anon_key='', has_supabase=False)


@app.route('/docs/<path:filename>')
def serve_docs(filename):
    """Serve static docs files."""
    # Resolve relative to this file: src/aida/web/app.py -> project_root/docs/
    docs_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'docs'))
    filepath = os.path.abspath(os.path.join(docs_dir, filename))
    if not filepath.startswith(docs_dir):
        return 'Forbidden', 403
    try:
        with open(filepath) as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    except FileNotFoundError:
        return 'Not found', 404


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


# === Analyses CRUD (Supabase) ===

@app.route('/api/analyses', methods=['POST'])
@require_supabase_auth
def create_analysis():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    data = request.json or {}
    row = {
        'user_id': request.user_id,
        'name': data.get('name', 'Nytt projekt'),
        'status': data.get('status', 'intake'),
        'project_data': data.get('project_data'),
        'baseline_data': data.get('baseline_data'),
        'alternatives_data': data.get('alternatives_data'),
        'selections_data': data.get('selections_data'),
        'report_markdown': data.get('report_markdown'),
    }
    result = supabase_request('POST', 'analyses', data=row, token=token)
    return jsonify(result[0] if isinstance(result, list) else result)


@app.route('/api/analyses', methods=['GET'])
@require_supabase_auth
def list_analyses():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    params = {
        'select': 'id,name,status,created_at,updated_at',
        'user_id': f'eq.{request.user_id}',
        'order': 'updated_at.desc',
        'limit': '20',
    }
    result = supabase_request('GET', 'analyses', token=token, params=params)
    return jsonify(result or [])


@app.route('/api/analyses/<analysis_id>', methods=['GET'])
@require_supabase_auth
def get_analysis(analysis_id):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    params = {
        'id': f'eq.{analysis_id}',
        'user_id': f'eq.{request.user_id}',
    }
    result = supabase_request('GET', 'analyses', token=token, params=params)
    if not result:
        return jsonify({'error': 'Ej hittad'}), 404
    return jsonify(result[0])


@app.route('/api/analyses/<analysis_id>', methods=['PUT'])
@require_supabase_auth
def update_analysis(analysis_id):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    data = request.json or {}
    update = {}
    for key in ('name', 'status', 'project_data', 'baseline_data',
                'alternatives_data', 'selections_data', 'report_markdown'):
        if key in data:
            update[key] = data[key]
    params = {
        'id': f'eq.{analysis_id}',
        'user_id': f'eq.{request.user_id}',
    }
    result = supabase_request('PATCH', 'analyses', data=update, token=token, params=params)
    if not result:
        return jsonify({'error': 'Ej hittad'}), 404
    return jsonify(result[0] if isinstance(result, list) else result)


@app.route('/api/analyses/<analysis_id>', methods=['DELETE'])
@require_supabase_auth
def delete_analysis(analysis_id):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    params = {
        'id': f'eq.{analysis_id}',
        'user_id': f'eq.{request.user_id}',
    }
    result = supabase_request('DELETE', 'analyses', token=token, params=params)
    if not result:
        return jsonify({'error': 'Ej hittad'}), 404
    return jsonify({'ok': True})


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AIda | Klimatkalkyl för ombyggnationer</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23E84E0D' stroke-width='1.5' stroke-linecap='round'><circle cx='12' cy='12' r='5'/><path d='M12 1v3M12 20v3M1 12h3M20 12h3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
{% if has_supabase %}<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>{% endif %}
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
.progress-line { position: absolute; top: 16px; left: 40px; right: 40px; height: 2px; background: var(--kk-gray-200); }
.progress-fill { position: absolute; top: 0; left: 0; height: 100%; background: var(--kk-charcoal); transition: width 0.5s ease; }
.step-item { display: flex; flex-direction: column; align-items: center; z-index: 1; min-width: 80px; }
.step-circle { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600; transition: all 0.3s; background: white; color: var(--kk-gray-400); border: 2px solid var(--kk-gray-200); }
.step-circle.active { background: var(--kk-charcoal); color: white; border-color: var(--kk-charcoal); box-shadow: 0 2px 8px rgba(68,68,68,0.4); transform: scale(1.1); }
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

/* === Auth overlay === */
#authOverlay { display: flex; align-items: center; justify-content: center; flex: 1; background: var(--kk-warm-bg); }
#authOverlay .login-box { background: white; border-radius: 12px; padding: 40px; width: 360px; box-shadow: 0 4px 24px rgba(0,0,0,0.08); border-top: 3px solid var(--kk-gold-light); }
#authOverlay .login-box h1 { font-size: 24px; color: var(--kk-charcoal); margin-bottom: 8px; }
#authOverlay .login-box p { font-size: 13px; color: var(--kk-gray-400); margin-bottom: 24px; }
#authOverlay .login-box input { width: 100%; padding: 12px 16px; border: 1px solid var(--kk-gray-200); border-radius: 8px; font-size: 14px; font-family: inherit; outline: none; margin-bottom: 8px; }
#authOverlay .login-box input:focus { border-color: var(--kk-dark-red); box-shadow: 0 0 0 2px rgba(181,32,31,0.15); }
#authOverlay .login-box button { width: 100%; padding: 12px; background: var(--kk-charcoal); color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; margin-top: 4px; font-family: inherit; }
#authOverlay .login-box button:hover { background: var(--kk-dark-red); }
#authOverlay .login-box button:disabled { opacity: 0.4; cursor: not-allowed; }
#authOverlay .error { color: var(--kk-dark-red); font-size: 12px; margin: 4px 0; }
#appContainer { display: flex; flex-direction: column; flex: 1; min-height: 0; }

/* === Dropdown menus === */
.project-btn { background: none; border: none; color: var(--kk-gray-500); font-size: 14px; cursor: pointer; display: flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 6px; font-family: inherit; }
.project-btn:hover { background: var(--kk-gray-100); color: var(--kk-charcoal); }
.user-btn { background: none; border: none; color: var(--kk-gray-400); cursor: pointer; padding: 6px; border-radius: 50%; display: flex; align-items: center; }
.user-btn:hover { background: var(--kk-gray-100); color: var(--kk-charcoal); }
.dropdown-menu { position: absolute; top: calc(100% + 4px); background: white; border: 1px solid var(--kk-gray-200); border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); min-width: 220px; z-index: 100; padding: 4px 0; }
.dropdown-right { right: 0; }
.dropdown-header { padding: 8px 16px; font-size: 11px; font-weight: 600; color: var(--kk-gray-400); text-transform: uppercase; }
.dropdown-divider { border-top: 1px solid var(--kk-gray-200); margin: 4px 0; }
.dropdown-item { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 16px; border: none; background: none; font-size: 13px; color: var(--kk-charcoal); cursor: pointer; font-family: inherit; text-align: left; }
.dropdown-item:hover { background: var(--kk-gray-50); }
.dropdown-item.active { background: var(--kk-gold-light); }

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

{% if has_supabase %}
<!-- Auth overlay -->
<div id="authOverlay">
  <div class="login-box">
    <h1>AIda</h1>
    <p>Klimatkalkyl och beslutsstöd för ombyggnationer</p>
    <input type="email" id="authEmail" placeholder="E-post" autofocus>
    <input type="password" id="authPassword" placeholder="Lösenord" onkeydown="if(event.key==='Enter')handleAuth()">
    <div id="authError" class="error" style="display:none"></div>
    <button onclick="handleAuth()" id="authSubmitBtn">Logga in</button>
    <div style="text-align:center;margin-top:12px;font-size:13px;color:var(--kk-gray-400)">
      <span id="authToggleText">Inget konto?</span>
      <a href="#" onclick="toggleAuthMode(event)" id="authToggleLink" style="color:var(--kk-dark-red)">Skapa konto</a>
    </div>
  </div>
</div>
<div id="appContainer">
{% endif %}

<!-- Top bar -->
<div class="topbar">
  <div class="topbar-logo">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v3M12 20v3M1 12h3M20 12h3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></svg>
    <span>AIda</span>
  </div>
  {% if has_supabase %}
  <div class="topbar-center" id="projectDropdown" style="position:relative">
    <button class="project-btn" onclick="toggleProjectMenu()" id="projectBtn">
      <span id="projectName">Nytt projekt</span>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
    </button>
    <div class="dropdown-menu" id="projectMenu" style="display:none;left:50%;transform:translateX(-50%)">
      <div class="dropdown-header">Senaste projekt</div>
      <div id="projectList"></div>
      <div class="dropdown-divider"></div>
      <button class="dropdown-item" onclick="createNewProject()">+ Skapa nytt projekt</button>
    </div>
  </div>
  <div class="topbar-right" id="userDropdown" style="position:relative">
    <button class="user-btn" onclick="toggleUserMenu()">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
    </button>
    <div class="dropdown-menu dropdown-right" id="userMenu" style="display:none">
      <div class="dropdown-header" id="userEmail"></div>
      <div class="dropdown-divider"></div>
      <button class="dropdown-item" onclick="handleLogout()">Logga ut</button>
    </div>
  </div>
  {% else %}
  <div class="topbar-center"></div>
  <div class="topbar-right">Prototyp</div>
  {% endif %}
</div>

<!-- Progress tracker -->
<div class="progress-bar">
  <div class="progress-track">
    <div class="progress-line"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
    <div class="step-item" data-step="planering">
      <div class="step-circle" id="sc-planering">1</div>
      <div class="step-label" id="sl-planering">Projektbeskrivning</div>
    </div>
    <div class="step-item" data-step="baslinje">
      <div class="step-circle" id="sc-baslinje">2</div>
      <div class="step-label" id="sl-baslinje">Baslinje</div>
    </div>
    <div class="step-item" data-step="aterbruk">
      <div class="step-circle" id="sc-aterbruk">3</div>
      <div class="step-label" id="sl-aterbruk">&#xC5;terbruk</div>
    </div>
    <div class="step-item" data-step="nyproduktion">
      <div class="step-circle" id="sc-nyproduktion">4</div>
      <div class="step-label" id="sl-nyproduktion">Nyproduktion</div>
    </div>
    <div class="step-item" data-step="sammanstallning">
      <div class="step-circle" id="sc-sammanstallning">5</div>
      <div class="step-label" id="sl-sammanstallning">Sammanst&#xE4;llning</div>
    </div>
    <div class="step-item" data-step="uppfoljning">
      <div class="step-circle" id="sc-uppfoljning">6</div>
      <div class="step-label" id="sl-uppfoljning">Uppf&#xF6;ljning</div>
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

{% if has_supabase %}</div><!-- /appContainer -->{% endif %}

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

function esc(s) { return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function renderMd(text) {
  text = text.replace(/(\d+)\)\s/g, (match, num, offset) => {
    return '\n' + num + '. ';
  }).trim();
  let html;
  if (typeof marked !== 'undefined') html = marked.parse(text);
  else html = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>');
  return typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
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
  const order = ['planering','baslinje','aterbruk','nyproduktion','sammanstallning','uppfoljning'];
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
  setProgressStep('planering');
  try {
    const r = await authFetch('/api/intake', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({description: desc})});
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
    if (HAS_SUPABASE) { document.getElementById('projectName').textContent = d.building_type || d.name || 'Nytt projekt'; }
    scheduleAutoSave();

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
  setProgressStep('baslinje');
  setLoading(true);
  try {
    const r = await authFetch('/api/baseline', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({project: state.project})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.baseline = d;
    state.alternatives = null;
    state.selections = {};
    state.reportMarkdown = null;
    state.step = 'baseline_done';
    scheduleAutoSave();

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
  addMsg('S\u00f6ker \u00e5terbruksalternativ...', 'system');
  setProgressStep('aterbruk');
  setLoading(true);
  const subStepTimer = setTimeout(() => {
    setProgressStep('nyproduktion');
    addMsg('S\u00f6ker klimatoptimerade alternativ...', 'system');
  }, 2000);
  try {
    const body = {project: state.project, baseline: state.baseline};
    if (userFeedback) body.user_feedback = userFeedback;
    const r = await authFetch('/api/alternatives', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const d = await r.json();
    clearTimeout(subStepTimer);
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.alternatives = d;
    state.selections = {};
    state.reportMarkdown = null;
    state.step = 'alternatives_done';
    scheduleAutoSave();
    setProgressStep('sammanstallning');

    enableTab('alternativ');
    switchTab('alternativ');
    document.getElementById('tab-rapport').disabled = true;

    const commentary = d.commentary || '';
    if (commentary) {
      addMsg(commentary, 'bot');
      addMsg('V\u00e4lj alternativ per komponent i resultatpanelen. Skriv i chatten om du vill ha fler f\u00f6rslag.', 'bot');
    } else {
      addMsg('Alternativ klara! V\u00e4lj per komponent i resultatpanelen.\n\nSkriv i chatten om du vill ha fler alternativ.', 'bot');
    }
    setLoading(false);
  } catch(e) { clearTimeout(subStepTimer); addMsg('Fel: ' + e.message, 'system'); setLoading(false); }
}

// === Pipeline: Report ===
async function generateReport() {
  setProgressStep('uppfoljning');
  addMsg('Genererar rapport...', 'system');
  document.getElementById('reportBtn').disabled = true;
  setLoading(true);
  try {
    const sels = {components: Object.values(state.selections)};
    const r = await authFetch('/api/report', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({project: state.project, selections: sels})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.reportMarkdown = d.markdown;
    state.step = 'report_done';
    scheduleAutoSave();
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
  html += '<div class="comp-card"><div class="comp-card-header"><h3>' + esc(d.building_type) + ', ' + esc(d.area_bta) + ' m\u00b2 BTA' + (d.name ? ' (' + esc(d.name) + ')' : '') + '</h3></div>';
  html += '<table class="comp-table"><thead><tr><th>Komponent</th><th>Antal</th><th>Enhet</th><th>Kategori</th></tr></thead><tbody>';
  d.components.forEach(c => {
    html += '<tr><td style="font-weight:500">' + esc(c.name) + '</td><td>' + esc(c.quantity) + '</td><td>' + esc(c.unit) + '</td><td>' + esc(c.category || '\u2013') + '</td></tr>';
  });
  html += '</tbody></table></div>';
  if (d.description) {
    html += '<div class="comp-card" style="margin-top:12px"><div class="comp-card-header"><h3>Beskrivning</h3></div><div style="padding:12px 16px;font-size:13px;color:var(--kk-gray-500);line-height:1.5">' + esc(d.description) + '</div></div>';
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
    html += '<tr><td style="font-weight:500">' + esc(c.component_name) + '</td><td style="text-align:right">' + Math.round(c.co2e_kg).toLocaleString('sv') + '</td><td style="text-align:right">' + Math.round(c.cost_sek).toLocaleString('sv') + '</td><td style="font-size:11px">' + formatSource(c.source) + '</td></tr>';
  });
  html += '</tbody></table></div>';
  document.getElementById('resultContent').innerHTML = html;
}

function renderAlternativContent() {
  const data = state.alternatives;
  let html = '<div class="section-title">J\u00e4mf\u00f6relse per komponent</div>';
  html += '<div class="source-legend"><span><span class="source-badge source-verified">EPD</span> Verifierad k\u00e4lla</span><span><span class="source-badge source-estimate">Est.</span> Uppskattning</span></div>';
  data.components.forEach(comp => {
    html += '<div class="comp-card"><div class="comp-card-header"><h3>' + esc(comp.component_name) + '</h3></div>';
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
        '<td style="font-weight:500">' + esc(alt.name) + '</td>' +
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
  scheduleAutoSave();
}

function updateSummary() {
  const sels = Object.values(state.selections);
  const totalCo2 = sels.reduce((s,c) => s + c.selected_alternative.co2e_kg, 0);
  const totalCost = sels.reduce((s,c) => s + c.selected_alternative.cost_sek, 0);
  const blCo2 = sels.reduce((s,c) => s + c.baseline_co2e_kg, 0);
  const blCost = sels.reduce((s,c) => s + c.baseline_cost_sek, 0);
  const co2Diff = totalCo2 - blCo2;
  const co2Pct = blCo2 > 0 ? Math.round(Math.abs(co2Diff) / blCo2 * 100) : 0;
  const co2Arrow = co2Diff <= 0 ? '\u2193' : '\u2191';
  const costDiff = totalCost - blCost;
  const costPct = blCost > 0 ? Math.round(Math.abs(costDiff) / blCost * 100) : 0;
  const costArrow = costDiff <= 0 ? '\u2193' : '\u2191';
  document.getElementById('summaryArea').innerHTML =
    '<div class="summary">' +
    '<div class="card' + (co2Diff <= 0 ? ' saving' : '') + '"><div class="card-title">Klimatp\u00e5verkan</div><div class="value">' + Math.round(totalCo2).toLocaleString('sv') + '</div><div class="sublabel">kg CO\u2082e (' + co2Arrow + co2Pct + '% vs baslinje)</div></div>' +
    '<div class="card' + (costDiff <= 0 ? ' saving' : '') + '"><div class="card-title">Kostnad</div><div class="value">' + Math.round(totalCost).toLocaleString('sv') + '</div><div class="sublabel">SEK (' + costArrow + costPct + '% vs baslinje)</div></div>' +
    '<div class="card"><div class="card-title">Baslinje</div><div class="value">' + Math.round(blCo2).toLocaleString('sv') + '</div><div class="sublabel">kg CO\u2082e | ' + Math.round(blCost).toLocaleString('sv') + ' SEK</div></div>' +
    '</div>';
  const allSelected = state.alternatives.components.every(c => state.selections[c.component_id]);
  document.getElementById('reportBtn').disabled = !allSelected;
}

// === Supabase auth + persistence ===
const HAS_SUPABASE = {{ 'true' if has_supabase else 'false' }};
const SUPABASE_URL = {{ supabase_url|tojson }};
const SUPABASE_ANON_KEY = {{ supabase_anon_key|tojson }};
let supabaseClient = null;
let currentUser = null;
let currentAnalysisId = null;
let isSignup = false;
let saveTimeout = null;
let saveInProgress = false;

// Auth-aware fetch wrapper
async function authFetch(url, options) {
  options = options || {};
  options.headers = options.headers || {};
  if (supabaseClient) {
    const sess = await supabaseClient.auth.getSession();
    if (sess.data.session) {
      options.headers['Authorization'] = 'Bearer ' + sess.data.session.access_token;
    }
  }
  return fetch(url, options);
}

// No-op when Supabase not configured
function scheduleAutoSave() {
  if (!HAS_SUPABASE || !currentUser) return;
  if (saveTimeout) clearTimeout(saveTimeout);
  saveTimeout = setTimeout(autoSave, 2000);
}

async function autoSave() {
  if (!supabaseClient || !currentUser || saveInProgress) return;
  saveInProgress = true;
  const analysisData = {
    name: state.project ? (state.project.name || state.project.building_type || 'Nytt projekt') : 'Nytt projekt',
    status: state.step,
    project_data: state.project,
    baseline_data: state.baseline,
    alternatives_data: state.alternatives,
    selections_data: Object.keys(state.selections).length > 0 ? state.selections : null,
    report_markdown: state.reportMarkdown,
  };
  try {
    if (currentAnalysisId) {
      const r = await authFetch('/api/analyses/' + currentAnalysisId, {
        method: 'PUT', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(analysisData),
      });
      await r.json();
    } else {
      const r = await authFetch('/api/analyses', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(analysisData),
      });
      const result = await r.json();
      if (result && result.id) {
        currentAnalysisId = result.id;
        await loadAnalysesList();
      }
    }
  } catch (e) { console.error('Auto-save failed:', e); }
  finally { saveInProgress = false; }
}

if (HAS_SUPABASE && SUPABASE_URL && SUPABASE_ANON_KEY) {
  supabaseClient = supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  initAuth();
}

async function initAuth() {
  const { data: { session } } = await supabaseClient.auth.getSession();
  if (session) { onLogin(session); }
  else { showAuth(); }
  supabaseClient.auth.onAuthStateChange((event, session) => {
    if (event === 'SIGNED_IN' && session) onLogin(session);
    else if (event === 'SIGNED_OUT') showAuth();
  });
}

function showAuth() {
  document.getElementById('authOverlay').style.display = 'flex';
  document.getElementById('appContainer').style.display = 'none';
}

function showApp() {
  document.getElementById('authOverlay').style.display = 'none';
  document.getElementById('appContainer').style.display = '';
}

async function onLogin(session) {
  currentUser = session.user;
  document.getElementById('userEmail').textContent = currentUser.email;
  showApp();
  const list = await loadAnalysesList();
  if (list && list.length > 0) { await loadAnalysis(list[0].id); }
}

async function handleAuth() {
  const email = document.getElementById('authEmail').value.trim();
  const password = document.getElementById('authPassword').value;
  const errorEl = document.getElementById('authError');
  errorEl.style.display = 'none';
  if (!email || !password) {
    errorEl.textContent = 'Fyll i e-post och lösenord';
    errorEl.style.display = 'block';
    return;
  }
  document.getElementById('authSubmitBtn').disabled = true;
  try {
    const result = isSignup
      ? await supabaseClient.auth.signUp({ email, password })
      : await supabaseClient.auth.signInWithPassword({ email, password });
    if (result.error) {
      errorEl.textContent = result.error.message;
      errorEl.style.display = 'block';
    } else if (isSignup && !result.data.session) {
      errorEl.textContent = 'Kolla din e-post för bekräftelselänk';
      errorEl.style.display = 'block';
      errorEl.style.color = 'var(--green-saving)';
    }
  } catch (e) {
    errorEl.textContent = e.message;
    errorEl.style.display = 'block';
  }
  document.getElementById('authSubmitBtn').disabled = false;
}

function toggleAuthMode(e) {
  e.preventDefault();
  isSignup = !isSignup;
  document.getElementById('authSubmitBtn').textContent = isSignup ? 'Skapa konto' : 'Logga in';
  document.getElementById('authToggleText').textContent = isSignup ? 'Har redan konto?' : 'Inget konto?';
  document.getElementById('authToggleLink').textContent = isSignup ? 'Logga in' : 'Skapa konto';
  document.getElementById('authError').style.display = 'none';
}

async function handleLogout() {
  await supabaseClient.auth.signOut();
  currentUser = null;
  currentAnalysisId = null;
  showAuth();
}

// === Project dropdown ===
function toggleProjectMenu() {
  const m = document.getElementById('projectMenu');
  const u = document.getElementById('userMenu');
  if (u) u.style.display = 'none';
  m.style.display = m.style.display === 'none' ? 'block' : 'none';
}

function toggleUserMenu() {
  const m = document.getElementById('userMenu');
  const p = document.getElementById('projectMenu');
  if (p) p.style.display = 'none';
  m.style.display = m.style.display === 'none' ? 'block' : 'none';
}

document.addEventListener('click', (e) => {
  const pd = document.getElementById('projectDropdown');
  const ud = document.getElementById('userDropdown');
  if (pd && !e.target.closest('#projectDropdown')) document.getElementById('projectMenu').style.display = 'none';
  if (ud && !e.target.closest('#userDropdown')) document.getElementById('userMenu').style.display = 'none';
});

async function loadAnalysesList() {
  if (!supabaseClient || !currentUser) return null;
  try {
    const r = await authFetch('/api/analyses');
    const list = await r.json();
    const container = document.getElementById('projectList');
    if (!container) return list;
    container.innerHTML = '';
    if (list && list.length > 0) {
      list.forEach(a => {
        const item = document.createElement('button');
        item.className = 'dropdown-item' + (a.id === currentAnalysisId ? ' active' : '');
        item.textContent = a.name || 'Nytt projekt';
        item.onclick = () => { loadAnalysis(a.id); toggleProjectMenu(); };
        container.appendChild(item);
      });
    } else {
      container.innerHTML = '<div style="padding:8px 16px;font-size:12px;color:var(--kk-gray-400)">Inga projekt ännu</div>';
    }
    return list;
  } catch(e) { console.error('Failed to load list:', e); return null; }
}

async function loadAnalysis(id) {
  if (saveTimeout) { clearTimeout(saveTimeout); saveTimeout = null; }
  try {
    const r = await authFetch('/api/analyses/' + id);
    const data = await r.json();
    if (!data || data.error) return;
    currentAnalysisId = id;
    state.project = data.project_data;
    state.baseline = data.baseline_data;
    state.alternatives = data.alternatives_data;
    state.selections = data.selections_data || {};
    state.reportMarkdown = data.report_markdown;
    state.step = data.status || 'idle';
    document.getElementById('projectName').textContent = data.name || 'Nytt projekt';
    restoreUI();
    await loadAnalysesList();
  } catch(e) { console.error('Failed to load analysis:', e); }
}

function restoreUI() {
  ['projekt','baslinje','alternativ','rapport'].forEach(t => {
    const el = document.getElementById('tab-' + t); if (el) el.disabled = true;
  });
  document.getElementById('progressFill').style.width = '0%';
  document.querySelectorAll('.step-circle').forEach((c, i) => { c.className = 'step-circle'; c.textContent = i + 1; });
  document.querySelectorAll('.step-label').forEach(l => l.className = 'step-label');

  if (state.project) { enableTab('projekt'); setProgressStep('planering'); }
  if (state.baseline) { enableTab('baslinje'); setProgressStep('baslinje'); }
  if (state.alternatives) { enableTab('alternativ'); setProgressStep('sammanstallning'); }
  if (state.reportMarkdown) { enableTab('rapport'); setProgressStep('uppfoljning'); switchTab('rapport'); }
  else if (state.alternatives) { switchTab('alternativ'); }
  else if (state.baseline) { switchTab('baslinje'); }
  else if (state.project) { switchTab('projekt'); }

  const msgs = document.getElementById('messages');
  msgs.innerHTML = '';
  if (!state.project) {
    addMsg('Hej! Beskriv ditt ombyggnadsprojekt. Ange byggnadstyp, ungefärlig yta och vilka åtgärder som ska göras.', 'bot');
  } else {
    addMsg('Projekt laddat: ' + (state.project.building_type || 'Okänt') + ', ' + (state.project.area_bta || '?') + ' m\u00b2.', 'bot');
    if (state.step === 'intake_done') addMsg('Bekräfta och beräkna baslinje, eller skriv korrigeringar.', 'bot');
    else if (state.step === 'baseline_done') addMsg('Baslinje klar. Bekräfta för att söka alternativ.', 'bot');
    else if (state.step === 'alternatives_done') addMsg('Välj alternativ per komponent i resultatpanelen.', 'bot');
    else if (state.step === 'report_done') addMsg('Rapporten är klar.', 'bot');
  }
}

function createNewProject() {
  toggleProjectMenu();
  currentAnalysisId = null;
  state = { project: null, baseline: null, alternatives: null, selections: {}, pendingDesc: null, reportMarkdown: null, step: 'idle' };
  document.getElementById('projectName').textContent = 'Nytt projekt';
  ['projekt','baslinje','alternativ','rapport'].forEach(t => {
    const el = document.getElementById('tab-' + t); if (el) el.disabled = true;
  });
  document.getElementById('resultTabs').style.display = 'none';
  document.getElementById('resultContent').innerHTML = '<div class="empty-state"><p>Beskriv ditt projekt i chatten till vänster för att börja.</p></div>';
  document.getElementById('progressFill').style.width = '0%';
  document.querySelectorAll('.step-circle').forEach((c, i) => { c.className = 'step-circle'; c.textContent = i + 1; });
  document.querySelectorAll('.step-label').forEach(l => l.className = 'step-label');
  const msgs = document.getElementById('messages');
  msgs.innerHTML = '';
  addMsg('Hej! Beskriv ditt ombyggnadsprojekt. Ange byggnadstyp, ungefärlig yta och vilka åtgärder som ska göras.', 'bot');
  setLoading(false);
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
