import os
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

from flask import Flask, abort, redirect, render_template_string, request, send_file, session, url_for

from storage import MODE, connect, initialize_store
from reporting import generate_monthly_report
from security import configure_security

app = Flask(__name__)
configure_security(app, MODE)


DEMO_USERS = {
    "demo.viewer": "Viewer",
    "demo.reviewer": "Reviewer",
    "demo.approver": "Approver",
    "demo.admin": "Administrator",
}

def initialize():
    initialize_store()
    if MODE != "demo":
        return
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        if count == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("INSERT", "Sales", "Customers", "Success", "INSERT INTO Sales.Customers (Name) VALUES ('Example Customer');"),
                ("UPDATE", "Sales", "Orders", "Success", "UPDATE Sales.Orders SET Status = 'Reviewed' WHERE OrderId = 1001;"),
                ("DELETE", "Sales", "TempQueue", "Success", "DELETE FROM Sales.TempQueue WHERE CreatedAt < '2026-01-01';"),
                ("CREATE", "dbo", "AuditDemo", "Success", "CREATE TABLE dbo.AuditDemo (Id int NOT NULL);"),
                ("ALTER", "dbo", "AuditDemo", "Success", "ALTER TABLE dbo.AuditDemo ALTER COLUMN Id bigint NOT NULL;"),
                ("DROP", "dbo", "OldAuditDemo", "Success", "DROP TABLE dbo.OldAuditDemo;"),
                ("TRUNCATE", "Sales", "ImportStage", "Success", "TRUNCATE TABLE Sales.ImportStage;"),
                ("UPDATE", "Finance", "Invoices", "Failed", "UPDATE Finance.Invoices SET Amount = -1 WHERE InvoiceId = 42;"),
                ("MERGE", "Sales", "Inventory", "Success", "MERGE Sales.Inventory AS t USING Sales.Import AS s ON t.Sku = s.Sku WHEN MATCHED THEN UPDATE SET t.Quantity = s.Quantity;"),
                ("ALTER", "dbo", "AuditDemo", "Success", "ALTER TABLE dbo.AuditDemo ADD Description nvarchar(200) NULL;"),
            ]
            for idx, (operation, schema, obj, outcome, statement) in enumerate(samples):
                event_time = (now.replace(microsecond=0) - __import__("datetime").timedelta(minutes=idx * 7)).isoformat()
                db.execute(
                    """INSERT INTO audit_events
                    (event_time, server_name, database_name, login_name, effective_user,
                     application_name, client_host, schema_name, object_name, operation, outcome, statement)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_time, "DEMO-SQL01", "AuditSample", "DOMAIN\\demo.user",
                     "dbo", "Audit Trail Demo", "DEMO-PC", schema, obj, operation, outcome, statement),
                )


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · SQL Server Audit Trail</title>
<style>
:root{font-family:Inter,Segoe UI,Arial,sans-serif;color:#152238;background:#f4f7fb}
body{margin:0}.top{background:#10243a;color:white;padding:18px 5vw;display:flex;justify-content:space-between;align-items:center}
main{max-width:1200px;margin:28px auto;padding:0 20px}.nav a{color:#dbeafe;margin-right:18px;text-decoration:none}
.card{background:white;border:1px solid #dce4ee;border-radius:10px;padding:20px;margin:14px 0;box-shadow:0 3px 14px #142b4610}
h1{font-size:25px}h2{font-size:18px}.muted{color:#64748b}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}
.metric{font-size:26px;font-weight:700;margin-top:8px}.button,button{background:#1769aa;border:0;color:#fff;padding:9px 13px;border-radius:6px;text-decoration:none;cursor:pointer}
input,select,textarea{padding:9px;border:1px solid #cbd5e1;border-radius:5px;margin:4px}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #e2e8f0;padding:9px;text-align:left;vertical-align:top}th{background:#f0f5fa}
pre{white-space:pre-wrap;word-break:break-word;margin:0;max-width:520px}.pill{padding:4px 8px;border-radius:12px;background:#e8f2ff}
.account-picker{display:inline-block;position:relative;vertical-align:middle;min-width:240px;margin:4px}.account-picker summary{cursor:pointer;padding:10px;border:1px solid #cbd5e1;border-radius:5px}.account-picker fieldset{position:absolute;z-index:2;background:white;min-width:300px;max-height:300px;overflow:auto;box-shadow:0 4px 16px #142b4620;border:1px solid #cbd5e1}
.alert{background:#fff7db;padding:12px;border-radius:6px}.actions{display:flex;gap:8px;align-items:center}
</style></head><body>
<header class="top"><div><b>SQL Server Audit Trail</b><div class="muted" style="color:#c3d3e5">{{ mode_label }}</div></div>
{% if user %}<div class="nav"><a href="{{ url_for('home') }}">Dashboard</a><a href="{{ url_for('events') }}">Audit trail</a><a href="{{ url_for('reports') }}">Reports</a>
<span>{{ user }} · {{ role }}</span> <form method="post" action="{{ url_for('logout') }}" style="display:inline"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button>Sign out</button></form></div>{% endif %}</header>
<main>{% with messages=get_flashed_messages() %}{% for msg in messages %}<div class="alert">{{ msg }}</div>{% endfor %}{% endwith %}
{{ body|safe }}</main></body></html>
"""


def page(title, body, **context):
    from markupsafe import escape
    from security import csrf_token
    rendered = render_template_string(body, **context)
    import re
    rendered = re.sub(r'(<form\b[^>]*method="post"[^>]*>)', lambda m: m.group(1) + '<input type="hidden" name="csrf_token" value="' + str(escape(csrf_token())) + '">', rendered, flags=re.I)
    return render_template_string(PAGE, title=title, body=rendered,
                                  user=session.get("user"), role=session.get("role"),
                                  mode_label="Demo mode · simulated events" if MODE == "demo" else "Live mode · SQL Server Audit")


@app.route("/login", methods=["GET", "POST"])
def login():
    if MODE == "live":
        from security import password_login
        error = password_login() if request.method == "POST" else None
        if error is False:
            return redirect(url_for("home"))
        return page("Sign in", """<div class="card"><h1>Sign in</h1>
        <p>Use an application account created by your administrator.</p>
        {% if error %}<p class="alert">{{ error }}</p>{% endif %}
        <form method="post"><label>Username <input name="username" required autocomplete="username"></label>
        <label>Password <input type="password" name="password" required autocomplete="current-password"></label>
        <button>Sign in</button></form></div>""", error=error)
    if request.method == "POST":
        username = request.form.get("username", "")
        if username in DEMO_USERS:
            session.clear()
            session["user"] = username
            session["role"] = DEMO_USERS[username]
            return redirect(url_for("home"))
    body = """
    <div class="card" style="max-width:480px;margin:60px auto"><h1>Sign in to demo</h1>
    <p class="muted">Choose a seeded demo identity. This login is for local demonstration only.</p>
    <form method="post"><label>Demo user</label><select name="username">
    {% for name, role in users.items() %}<option value="{{ name }}">{{ name }} — {{ role }}</option>{% endfor %}
    </select><button>Continue</button></form></div>"""
    return page("Sign in", body, users=DEMO_USERS)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def home():
    with connect() as db:
        total = db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        recent = db.execute("SELECT * FROM audit_events ORDER BY event_time DESC LIMIT 8").fetchall()
        pending = db.execute("SELECT COUNT(*) FROM reports WHERE status='Pending'").fetchone()[0]
    body = """
    <h1>Audit dashboard</h1><p class="muted">{{ source_message }}</p>
    <div class="grid"><div class="card">Audit records<div class="metric">{{ total }}</div></div>
    <div class="card">Pending report reviews<div class="metric">{{ pending }}</div></div>
    <div class="card">Collector status<div class="metric" style="font-size:18px">{{ collector_status }}</div></div></div>
    <div class="card"><h2>Recent activity</h2>{{ table|safe }}</div>"""
    with connect() as db:
        states = db.execute("SELECT * FROM collector_state ORDER BY source_id").fetchall()
    message = "Demo data is simulated; no SQL Server is connected." if MODE == "demo" else "Live audit records. Collector health does not establish complete statement coverage."
    body += """<div class="card"><h2>Collection health</h2>
    {% for state in states %}<p><b>{{ state.source_id }}: {{ state.status }}</b><br>
    Last attempt: {{ state.last_attempt or 'Never' }} · Last successful scan: {{ state.last_success or 'Never' }}<br>
    {{ state.detail }}<br>Version: {{ state.server_version or 'Unknown' }} · {{ state.edition or '' }}</p>
    {% else %}<p>No collector has reported yet. Run python collector.py --check, then python collector.py.</p>{% endfor %}
    <p>Check the last-attempt time: a stopped worker cannot update its status. Retention gaps and unaudited statements cannot be reconstructed.</p></div>"""
    return page("Dashboard", body, total=total, pending=pending, table=event_table(recent),
                states=states, source_message=message, collector_status="See health below" if MODE == "live" else "Demo only")


def event_table(rows):
    return render_template_string("""{% if not rows %}<p>No audit records found.</p>{% else %}
    <div style="overflow:auto"><table><tr><th>Event time (UTC)</th><th>Operation / action</th><th>Server / database / object</th>
    <th>Account / effective user</th><th>Audit result</th><th>SQL / evidence</th></tr>
    {% for r in rows %}<tr><td>{{ r.event_time }}</td><td>{{ r.operation }}</td>
    <td>{{ r.server_name }}<br>{{ r.database_name }}.{{ r.schema_name }}.{{ r.object_name }}</td>
    <td>{{ r.login_name }}<br>{{ r.effective_user }}</td><td>{{ r.outcome }}</td>
    <td style="min-width:320px"><pre>{{ r.statement or 'No SQL text supplied by source.' }}</pre>
    <a href="{{ url_for('event_detail', event_id=r.id) }}">Full record</a></td></tr>{% endfor %}</table></div>{% endif %}""", rows=rows)


@app.get("/events/<int:event_id>")
@login_required
def event_detail(event_id):
    with connect() as db:
        row = db.execute("SELECT * FROM audit_events WHERE id=?", (event_id,)).fetchone()
        source = db.execute("SELECT * FROM source_records WHERE event_id=?", (event_id,)).fetchone()
    if not row:
        abort(404)
    return page("Audit evidence", """<h1>Audit record {{ row.id }}</h1><div class="card">
    {% for key in row.keys() %}<p><b>{{ key }}</b>: <span style="white-space:pre-wrap">{{ row[key] }}</span></p>{% endfor %}</div>
    {% if source %}<div class="card"><h2>Source evidence</h2>
    <p>File: {{ source.file_name }} · offset: {{ source.file_offset }} · sequence: {{ source.sequence_number }} · group: {{ source.sequence_group_id }}</p>
    <p>SQL text may be one fragment of a larger record. All available fragments are retained separately; sequence metadata is included in CSV.</p>
    <pre>{{ source.raw_json }}</pre></div>{% endif %}""", row=row, source=source)


@app.get("/events")
@login_required
def events():
    selected_accounts = list(dict.fromkeys(
        value for value in request.args.getlist("account") if value
    ))
    selected_keys = {value.lower() for value in selected_accounts}
    query = "SELECT * FROM audit_events"
    params = selected_accounts
    if selected_accounts:
        placeholders = ",".join("?" for _ in selected_accounts)
        query += " WHERE login_name COLLATE NOCASE IN (" + placeholders + ")"
    query += " ORDER BY event_time DESC, id DESC LIMIT 500"
    with connect() as db:
        rows = db.execute(query, params).fetchall()
        accounts = [r[0] for r in db.execute(
            "SELECT DISTINCT login_name FROM audit_events WHERE login_name IS NOT NULL AND login_name != '' ORDER BY login_name COLLATE NOCASE"
        ).fetchall()]
    body = """
    <h1>Audit trail</h1><p>Latest 500 matching records. Monthly CSV includes all collected records for the period.</p>
    <div class="card"><form method="get">
    <details class="account-picker"><summary>Accounts: {{ selected_accounts|length|string + ' selected' if selected_accounts else 'All accounts' }}</summary>
    <fieldset><legend>Select accounts</legend>
    <p class="muted">Leave all unchecked to show all accounts.</p>
    {% for name in accounts %}<label style="display:block;padding:4px">
    <input type="checkbox" name="account" value="{{ name }}" {% if name|lower in selected_keys %}checked{% endif %}>{{ name }}</label>
    {% else %}<p>No accounts collected yet.</p>{% endfor %}
    </fieldset></details>
    <button>Filter</button>
    <a class="button" href="{{ url_for('events') }}">Clear</a></form></div>
    <div class="card">{{ table|safe }}</div>"""
    return page("Audit trail", body, selected_accounts=selected_accounts,
                selected_keys=selected_keys, accounts=accounts, table=event_table(rows))


@app.get("/reports")
@login_required
def reports():
    with connect() as db:
        rows = db.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()
        reviews = db.execute("SELECT * FROM report_reviews ORDER BY decided_at DESC").fetchall()
    with connect() as db:
        schedule_state = db.execute("SELECT * FROM report_scheduler_state WHERE id=1").fetchone()
    schedule_enabled = os.environ.get("REPORT_SCHEDULE_ENABLED", "false").lower() == "true"
    body = """
    <h1>Monthly reports</h1>
    <div class="card"><h2>Automatic monthly reporting</h2>
    <p>{{ 'Enabled' if schedule_enabled else 'Disabled' }} · Calendar months in UTC.
    {% if schedule_enabled %}First month: {{ schedule_start }} · Delay after month end: {{ schedule_delay }} hour(s).
    Requires the collector to be running and a successful full scan.{% endif %}</p>
    {% if schedule_state %}<p>Status: {{ schedule_state.status }} · Last attempt (UTC): {{ schedule_state.last_attempt }}
    <br>Last successful reporting check (UTC): {{ schedule_state.last_success or 'Never' }}<br>{{ schedule_state.detail }}</p>{% endif %}
    <p>Scheduled reports await human approval. Reports are snapshots; records collected later require a new report version.</p></div>
    <p class="alert">Reports contain collected audit records only. Capture coverage is not yet certified. Long SQL may span multiple records; approval does not certify capture completeness.</p><div class="card"><h2>Generate CSV</h2>
    <form method="post" action="{{ url_for('generate_report') }}"><label>Month</label>
    <input type="month" name="period" required value="{{ current_month }}">
    <button>Generate report</button></form><p class="muted">Each generation creates a new version and records its SHA-256 digest.</p></div>
    <div class="card"><h2>Report history</h2>{% if rows %}<table><tr><th>Period</th><th>Version</th><th>Created</th><th>Records</th><th>Status</th><th>Digest</th><th>Actions</th></tr>
    {% for r in rows %}<tr><td>{{ r.period }}</td><td>v{{ r.version }}</td><td>{{ r.created_at }}<br>by {{ r.created_by }}</td><td>{{ r.event_count }}</td><td>{{ r.status }}</td><td><code>{{ r.sha256[:14] }}…</code></td><td><a href="{{ url_for('download_report', report_id=r.id) }}">CSV</a>
    {% if r.status=='Pending' and r.created_by != user and role in ['Reviewer','Approver','Administrator'] %}<form class="actions" method="post" action="{{ url_for('decide_report', report_id=r.id) }}">
    <input name="comment" placeholder="Optional comment"><button name="decision" value="Approved">Approve</button><button name="decision" value="Rejected">Reject</button></form>{% endif %}</td></tr>{% endfor %}</table>
    {% else %}<p class="muted">No reports generated yet.</p>{% endif %}</div>
    <div class="card"><h2>Decision history</h2><table><tr><th>Report ID</th><th>User</th><th>Decision</th><th>When (UTC)</th><th>Comment</th></tr>
    {% for item in reviews %}<tr><td>{{ item.report_id }}</td><td>{{ item.reviewer }}</td><td>{{ item.decision }}</td><td>{{ item.decided_at }}</td><td>{{ item.comment }}</td></tr>{% endfor %}</table></div>"""
    return page("Reports", body, rows=rows, reviews=reviews, role=session["role"], user=session["user"], current_month=datetime.now(timezone.utc).strftime("%Y-%m"),
                schedule_enabled=schedule_enabled, schedule_state=schedule_state,
                schedule_start=os.environ.get("REPORT_SCHEDULE_START_MONTH", "Not configured"),
                schedule_delay=os.environ.get("REPORT_SCHEDULE_DELAY_HOURS", "1"))


@app.post("/reports/generate")
@roles_required("Reviewer", "Approver", "Administrator")
def generate_report():
    period = request.form.get("period", "")
    from reporting import month_bounds
    try:
        month_bounds(period)
    except ValueError:
        abort(400)
    generate_monthly_report(period, session["user"])
    return redirect(url_for("reports"))


@app.get("/reports/<int:report_id>/download")
@login_required
def download_report(report_id):
    with connect() as db:
        report = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not report or not Path(report["file_path"]).is_file():
        abort(404)
    return send_file(report["file_path"], as_attachment=True, download_name=Path(report["file_path"]).name, mimetype="text/csv")


@app.post("/reports/<int:report_id>/decision")
@roles_required("Reviewer", "Approver", "Administrator")
def decide_report(report_id):
    decision = request.form.get("decision")
    if decision not in ("Approved", "Rejected"):
        abort(400)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        report = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not report or report["status"] != "Pending":
            abort(409)
        if report["created_by"] == session["user"]:
            abort(403)
        db.execute("INSERT INTO report_reviews(report_id,reviewer,decision,comment,decided_at) VALUES(?,?,?,?,?)",
                   (report_id, session["user"], decision, request.form.get("comment","").strip(),
                    datetime.now(timezone.utc).isoformat()))
        db.execute("UPDATE reports SET status=? WHERE id=?", (decision, report_id))
    return redirect(url_for("reports"))


@app.errorhandler(403)
def forbidden(_error):
    return page("Access denied", "<div class='card'><h1>Access denied</h1><p>Your role cannot perform this action, or you are reviewing your own report.</p></div>"), 403


@app.errorhandler(409)
def conflict(_error):
    return page("Already reviewed", "<div class='card'><h1>Report already reviewed</h1><p>Generate a new version if it needs another review.</p></div>"), 409


initialize()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)

