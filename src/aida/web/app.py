"""AIda Web UI — Flask app with split chat/results layout."""

from __future__ import annotations

import json
import sys
import os
from flask import Flask, request, jsonify, render_template_string

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from aida.models import Project, Baseline, Selections
from aida.agents.intake import run_intake
from aida.agents.baseline import calculate_baseline
from aida.agents.alternatives import find_alternatives
from aida.agents.aggregate import compute_aggregate
from aida.agents.report import generate_report_markdown

app = Flask(__name__)

# In-memory session state
sessions: dict[str, dict] = {}


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/intake', methods=['POST'])
def api_intake():
    data = request.json
    description = data.get('description', '')
    if not description:
        return jsonify({'error': 'Beskrivning saknas'}), 400

    try:
        result = run_intake(description)
        session_id = str(hash(description))[:8]
        sessions[session_id] = {'project_data': result, 'step': 'intake'}
        return jsonify({'session_id': session_id, **result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/baseline', methods=['POST'])
def api_baseline():
    data = request.json
    session_id = data.get('session_id', '')
    session = sessions.get(session_id, {})
    project_data = data.get('project', session.get('project_data'))

    if not project_data:
        return jsonify({'error': 'Projekt saknas'}), 400

    try:
        project = Project.from_dict(project_data)
        baseline = calculate_baseline(project)
        if session_id in sessions:
            sessions[session_id]['baseline'] = baseline.to_dict()
            sessions[session_id]['step'] = 'baseline'
        return jsonify(baseline.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alternatives', methods=['POST'])
def api_alternatives():
    data = request.json
    session_id = data.get('session_id', '')
    session = sessions.get(session_id, {})

    project_data = data.get('project', session.get('project_data'))
    baseline_data = data.get('baseline', session.get('baseline'))

    if not project_data or not baseline_data:
        return jsonify({'error': 'Projekt eller baslinje saknas'}), 400

    try:
        project = Project.from_dict(project_data)
        baseline = Baseline.from_dict(baseline_data)
        result = find_alternatives(project, baseline)
        if session_id in sessions:
            sessions[session_id]['alternatives'] = result.to_dict()
            sessions[session_id]['step'] = 'alternatives'
        return jsonify(result.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/aggregate', methods=['POST'])
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
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; background: #f5f5f5; color: #1a1a1a; }
header { background: #1a5632; color: white; padding: 12px 24px; display: flex; align-items: center; gap: 12px; }
header h1 { font-size: 18px; font-weight: 600; }
header span { font-size: 13px; opacity: 0.8; }
.main { display: flex; flex: 1; overflow: hidden; }
.chat-panel { width: 40%; display: flex; flex-direction: column; border-right: 1px solid #ddd; background: white; }
.results-panel { width: 60%; display: flex; flex-direction: column; overflow-y: auto; padding: 24px; }
.messages { flex: 1; overflow-y: auto; padding: 16px; }
.msg { margin-bottom: 12px; padding: 10px 14px; border-radius: 12px; max-width: 85%; font-size: 14px; line-height: 1.5; }
.msg.user { background: #e8f5e9; margin-left: auto; border-bottom-right-radius: 4px; }
.msg.bot { background: #f0f0f0; border-bottom-left-radius: 4px; }
.msg.system { background: #fff3e0; font-size: 13px; text-align: center; max-width: 100%; }
.input-area { padding: 12px; border-top: 1px solid #eee; display: flex; gap: 8px; }
.input-area textarea { flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 8px; resize: none; font-size: 14px; font-family: inherit; height: 44px; }
.input-area button { padding: 10px 20px; background: #1a5632; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }
.input-area button:disabled { opacity: 0.5; cursor: not-allowed; }
.input-area button:hover:not(:disabled) { background: #2d7a4a; }

/* Progress */
.progress { display: flex; gap: 4px; margin-bottom: 20px; }
.step { flex: 1; padding: 8px; text-align: center; font-size: 12px; border-radius: 6px; background: #eee; color: #888; }
.step.active { background: #1a5632; color: white; }
.step.done { background: #c8e6c9; color: #1a5632; }

/* Components table */
.comp-table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13px; }
.comp-table th { background: #f5f5f5; padding: 10px; text-align: left; border-bottom: 2px solid #ddd; }
.comp-table td { padding: 10px; border-bottom: 1px solid #eee; }
.comp-table tr:hover { background: #fafafa; }
.alt-row { cursor: pointer; }
.alt-row.selected { background: #e8f5e9 !important; }
.alt-row input[type=radio] { margin-right: 6px; }

/* Summary card */
.summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 16px 0; }
.card { background: white; border: 1px solid #ddd; border-radius: 10px; padding: 16px; text-align: center; }
.card .value { font-size: 28px; font-weight: 700; color: #1a5632; }
.card .label { font-size: 12px; color: #666; margin-top: 4px; }
.card.saving .value { color: #2e7d32; }

.section-title { font-size: 16px; font-weight: 600; margin: 20px 0 8px; color: #333; }
.btn { padding: 10px 20px; background: #1a5632; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; margin-top: 12px; }
.btn:hover { background: #2d7a4a; }
.btn:disabled { opacity: 0.5; }
.hidden { display: none; }
.report-area { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin-top: 16px; white-space: pre-wrap; font-size: 13px; line-height: 1.6; max-height: 500px; overflow-y: auto; }
</style>
</head>
<body>
<header>
  <h1>AIda</h1>
  <span>Klimatkalkyl och beslutsstöd för ombyggnationer</span>
</header>
<div class="main">
  <div class="chat-panel">
    <div class="messages" id="messages">
      <div class="msg bot">Hej! Beskriv ditt ombyggnadsprojekt. Ange gärna byggnadstyp, ungefärlig yta och vilka åtgärder som ska göras.</div>
    </div>
    <div class="input-area">
      <textarea id="userInput" placeholder="T.ex. Vi ska renovera skolkök, ca 200 kvm..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage()}"></textarea>
      <button id="sendBtn" onclick="sendMessage()">Skicka</button>
    </div>
  </div>
  <div class="results-panel" id="results">
    <div class="progress" id="progress">
      <div class="step" data-step="intake">1. Projektintake</div>
      <div class="step" data-step="baseline">2. Baslinje</div>
      <div class="step" data-step="alternatives">3. Alternativ</div>
      <div class="step" data-step="select">4. Välj</div>
      <div class="step" data-step="report">5. Rapport</div>
    </div>
    <div id="resultContent">
      <p style="color:#888;text-align:center;margin-top:60px;">Beskriv ditt projekt i chatten till vänster för att börja.</p>
    </div>
  </div>
</div>

<script>
let state = { sessionId: null, project: null, baseline: null, alternatives: null, selections: {} };

function addMsg(text, cls) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  document.getElementById('messages').appendChild(d);
  d.scrollIntoView({behavior:'smooth'});
}

function setStep(name) {
  document.querySelectorAll('.step').forEach(s => {
    const order = ['intake','baseline','alternatives','select','report'];
    const si = order.indexOf(s.dataset.step);
    const ni = order.indexOf(name);
    s.className = 'step' + (si < ni ? ' done' : si === ni ? ' active' : '');
  });
}

function setLoading(on) {
  document.getElementById('sendBtn').disabled = on;
  document.getElementById('userInput').disabled = on;
}

async function sendMessage() {
  const input = document.getElementById('userInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addMsg(text, 'user');
  setLoading(true);

  if (!state.project) {
    await runIntake(text);
  }
}

async function runIntake(desc) {
  addMsg('Analyserar projektbeskrivning...', 'system');
  setStep('intake');
  try {
    const r = await fetch('/api/intake', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({description: desc})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.sessionId = d.session_id;
    state.project = d;
    const comps = d.components.map(c => c.name).join(', ');
    addMsg(`Identifierade: ${d.building_type}, ${d.area_bta} m², komponenter: ${comps}`, 'bot');
    if (d.clarification_needed) {
      addMsg(d.clarification_needed, 'bot');
      setLoading(false);
      return;
    }
    await runBaseline();
  } catch(e) { addMsg('Fel: ' + e.message, 'system'); setLoading(false); }
}

async function runBaseline() {
  addMsg('Beräknar baslinje (NollCO2)...', 'system');
  setStep('baseline');
  try {
    const r = await fetch('/api/baseline', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({session_id: state.sessionId, project: state.project})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.baseline = d;
    const total = d.components.reduce((s,c) => s + c.co2e_kg, 0);
    addMsg(`Baslinje klar: ${Math.round(total)} kg CO2e totalt för ${d.components.length} komponenter.`, 'bot');
    await runAlternatives();
  } catch(e) { addMsg('Fel: ' + e.message, 'system'); setLoading(false); }
}

async function runAlternatives() {
  addMsg('Söker klimatsmarta alternativ...', 'system');
  setStep('alternatives');
  try {
    const r = await fetch('/api/alternatives', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({session_id: state.sessionId, project: state.project, baseline: state.baseline})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); setLoading(false); return; }
    state.alternatives = d;
    addMsg('Alternativ klara! Välj alternativ per komponent i resultatpanelen.', 'bot');
    setStep('select');
    renderAlternatives(d);
    setLoading(false);
  } catch(e) { addMsg('Fel: ' + e.message, 'system'); setLoading(false); }
}

function renderAlternatives(data) {
  let html = '<div class="section-title">Jämförelse per komponent</div>';
  data.components.forEach(comp => {
    html += `<h3 style="margin:16px 0 8px;font-size:14px;">${comp.component_name}</h3>`;
    html += '<table class="comp-table"><tr><th></th><th>Alternativ</th><th>CO2e (kg)</th><th>Kostnad (SEK)</th><th>Besparing</th><th>Källa</th></tr>';
    // Baseline row
    html += `<tr class="alt-row" onclick="selectAlt('${comp.component_id}','baseline',this)">
      <td><input type="radio" name="${comp.component_id}"></td>
      <td>Baslinje (konventionellt)</td>
      <td>${Math.round(comp.baseline_co2e_kg)}</td>
      <td>${Math.round(comp.baseline_cost_sek).toLocaleString('sv')}</td>
      <td>-</td><td>NollCO2</td></tr>`;
    comp.alternatives.forEach((alt, i) => {
      const saving = Math.round((1 - alt.co2e_kg / comp.baseline_co2e_kg) * 100);
      html += `<tr class="alt-row" onclick="selectAlt('${comp.component_id}','${i}',this)">
        <td><input type="radio" name="${comp.component_id}"></td>
        <td>${alt.name}</td>
        <td>${Math.round(alt.co2e_kg)}</td>
        <td>${Math.round(alt.cost_sek).toLocaleString('sv')}</td>
        <td style="color:#2e7d32">${saving}%</td>
        <td style="font-size:11px">${alt.source}</td></tr>`;
    });
    html += '</table>';
  });
  html += '<div id="summaryArea"></div>';
  html += '<button class="btn" id="reportBtn" onclick="generateReport()" disabled>Generera rapport</button>';
  document.getElementById('resultContent').innerHTML = html;
}

function selectAlt(compId, altIdx, row) {
  // Visual
  row.closest('table').querySelectorAll('.alt-row').forEach(r => r.classList.remove('selected'));
  row.classList.add('selected');
  row.querySelector('input[type=radio]').checked = true;

  // State
  const comp = state.alternatives.components.find(c => c.component_id === compId);
  if (altIdx === 'baseline') {
    state.selections[compId] = {
      id: compId, name: comp.component_name,
      selected_alternative: {name:'Baslinje', co2e_kg: comp.baseline_co2e_kg, cost_sek: comp.baseline_cost_sek, source:'NollCO2'},
      baseline_co2e_kg: comp.baseline_co2e_kg, baseline_cost_sek: comp.baseline_cost_sek
    };
  } else {
    const alt = comp.alternatives[parseInt(altIdx)];
    state.selections[compId] = {
      id: compId, name: comp.component_name,
      selected_alternative: {name: alt.name, co2e_kg: alt.co2e_kg, cost_sek: alt.cost_sek, source: alt.source},
      baseline_co2e_kg: comp.baseline_co2e_kg, baseline_cost_sek: comp.baseline_cost_sek
    };
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

  document.getElementById('summaryArea').innerHTML = `
    <div class="summary">
      <div class="card"><div class="value">${Math.round(totalCo2).toLocaleString('sv')}</div><div class="label">kg CO2e (valt)</div></div>
      <div class="card saving"><div class="value">${Math.round(saving).toLocaleString('sv')}</div><div class="label">kg CO2e besparing (${pct}%)</div></div>
      <div class="card"><div class="value">${Math.round(totalCost).toLocaleString('sv')}</div><div class="label">SEK total kostnad</div></div>
    </div>`;

  const allSelected = state.alternatives.components.every(c => state.selections[c.component_id]);
  document.getElementById('reportBtn').disabled = !allSelected;
}

async function generateReport() {
  setStep('report');
  addMsg('Genererar rapport...', 'system');
  document.getElementById('reportBtn').disabled = true;
  try {
    const sels = {components: Object.values(state.selections)};
    const r = await fetch('/api/report', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({project: state.project, selections: sels})});
    const d = await r.json();
    if (d.error) { addMsg('Fel: ' + d.error, 'system'); return; }
    addMsg('Rapport klar!', 'bot');
    const area = document.createElement('div');
    area.className = 'report-area';
    area.textContent = d.markdown;
    document.getElementById('summaryArea').after(area);

    const dlBtn = document.createElement('button');
    dlBtn.className = 'btn';
    dlBtn.textContent = 'Ladda ner rapport (.md)';
    dlBtn.onclick = () => {
      const blob = new Blob([d.markdown], {type:'text/markdown'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'aida-rapport.md';
      a.click();
    };
    area.after(dlBtn);
  } catch(e) { addMsg('Fel: ' + e.message, 'system'); }
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
