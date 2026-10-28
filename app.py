import csv
import hashlib
import io
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

from flask import Flask, abort, redirect, render_template_string, request, send_file, session, url_for

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = Path(os.environ.get("AUDIT_INSTANCE_DIR", BASE_DIR / "instance"))
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR", INSTANCE_DIR / "reports"))
DB_PATH = Path(os.environ.get("AUDIT_DB_PATH", INSTANCE_DIR / "audit-demo.sqlite3"))
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "local-demo-only-change-this")


DEMO_USERS = {
    "demo.viewer": "Viewer",
    "demo.reviewer": "Reviewer",
    "demo.approver": "Approver",
    "demo.admin": "Administrator",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT NOT NULL,
    server_name TEXT NOT NULL,
    database_name TEXT NOT NULL,
    login_name TEXT NOT NULL,
    effective_user TEXT NOT NULL,
    application_name TEXT NOT NULL,
    client_host TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    object_name TEXT NOT NULL,
    operation TEXT NOT NULL,
    outcome TEXT NOT NULL,
    statement TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',
    UNIQUE(period, version)
);
CREATE TABLE IF NOT EXISTS report_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
"""


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize():
    with connect() as db:
        db.executescript(SCHEMA)
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
.alert{background:#fff7db;padding:12px;border-radius:6px}.actions{display:flex;gap:8px;align-items:center}
</style></head><body>
<header class="top"><div><b>SQL Server Audit Trail</b><div class="muted" style="color:#c3d3e5">Demo mode · simulated events</div></div>
{% if user %}<div class="nav"><a href="{{ url_for('home') }}">Dashboard</a><a href="{{ url_for('events') }}">Audit trail</a><a href="{{ url_for('reports') }}">Reports</a>
<span>{{ user }} · {{ role }}</span> <a href="{{ url_for('logout') }}">Sign out</a></div>{% endif %}</header>
<main>{% with messages=get_flashed_messages() %}{% for msg in messages %}<div class="alert">{{ msg }}</div>{% endfor %}{% endwith %}
{{ body|safe }}</main></body></html>
"""


def page(title, body, **context):
    return render_template_string(PAGE, title=title, body=render_template_string(body, **context),
                                  user=session.get("user"), role=session.get("role"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        if username in DEMO_USERS:
            session.clear()
            session["user"] = username
            session["role"] = DEMO_USERS[username]
            return redirect(request.args.get("next") or url_for("home"))
    body = """
    <div class="card" style="max-width:480px;margin:60px auto"><h1>Sign in to demo</h1>
    <p class="muted">Choose a seeded demo identity. This login is for local demonstration only.</p>
    <form method="post"><label>Demo user</label><select name="username">
    {% for name, role in users.items() %}<option value="{{ name }}">{{ name }} — {{ role }}</option>{% endfor %}
    </select><button>Continue</button></form></div>"""
    return page("Sign in", body, users=DEMO_USERS)


@app.get("/logout")
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
    <h1>Audit dashboard</h1><p class="muted">Demo data is simulated; no SQL Server is connected.</p>
    <div class="grid"><div class="card">Captured events<div class="metric">{{ total }}</div></div>
    <div class="card">Pending report reviews<div class="metric">{{ pending }}</div></div>
    <div class="card">Collector status<div class="metric" style="font-size:18px">Demo only</div></div></div>
    <div class="card"><h2>Recent activity</h2>{{ table|safe }}</div>"""
    return page("Dashboard", body, total=total, pending=pending, table=event_table(recent))


def event_table(rows):
    if not rows:
        return "<p>No audit events found.</p>"
    out = ["<table><thead><tr><th>When (UTC)</th><th>Operation</th><th>Database / object</th><th>Login / account</th><th>Outcome</th><th>SQL statement</th></tr></thead><tbody>"]
    for row in rows:
        out.append("<tr><td>{}</td><td><span class='pill'>{}</span></td><td>{}.{}.{}</td><td>{}<br>{}</td><td>{}</td><td><details><summary>View SQL</summary><pre>{}</pre></details></td></tr>".format(
            row["event_time"], row["operation"], row["database_name"], row["schema_name"], row["object_name"],
            row["login_name"], row["effective_user"], row["outcome"], row["statement"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")))
    out.append("</tbody></table>")
    return "".join(out)


@app.get("/events")
@login_required
def events():
    operation = request.args.get("operation", "")
    with connect() as db:
        if operation:
            rows = db.execute("SELECT * FROM audit_events WHERE operation=? ORDER BY event_time DESC", (operation,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM audit_events ORDER BY event_time DESC").fetchall()
    body = """
    <h1>Complete audit trail</h1>
    <div class="card"><form method="get"><label>Operation</label><select name="operation"><option value="">All</option>
    {% for op in operations %}<option {% if selected==op %}selected{% endif %}>{{ op }}</option>{% endfor %}</select><button>Filter</button>
    <a class="button" href="{{ url_for('events') }}">Clear</a></form></div>
    <div class="card">{{ table|safe }}</div>"""
    return page("Audit trail", body, operations=["INSERT","UPDATE","DELETE","MERGE","CREATE","ALTER","DROP","TRUNCATE"],
                selected=operation, table=event_table(rows))


@app.get("/reports")
@login_required
def reports():
    with connect() as db:
        rows = db.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()
    body = """
    <h1>Monthly reports</h1><div class="card"><h2>Generate CSV</h2>
    <form method="post" action="{{ url_for('generate_report') }}"><label>Month</label>
    <input type="month" name="period" required value="{{ current_month }}">
    <button>Generate report</button></form><p class="muted">Each generation creates a new version and records its SHA-256 digest.</p></div>
    <div class="card"><h2>Report history</h2>{% if rows %}<table><tr><th>Period</th><th>Version</th><th>Created</th><th>Events</th><th>Status</th><th>Digest</th><th>Actions</th></tr>
    {% for r in rows %}<tr><td>{{ r.period }}</td><td>v{{ r.version }}</td><td>{{ r.created_at }}<br>by {{ r.created_by }}</td><td>{{ r.event_count }}</td><td>{{ r.status }}</td><td><code>{{ r.sha256[:14] }}…</code></td><td><a href="{{ url_for('download_report', report_id=r.id) }}">CSV</a>
    {% if r.status=='Pending' and role in ['Reviewer','Approver','Administrator'] %}<form class="actions" method="post" action="{{ url_for('decide_report', report_id=r.id) }}">
    <input name="comment" placeholder="Optional comment"><button name="decision" value="Approved">Approve</button><button name="decision" value="Rejected">Reject</button></form>{% endif %}</td></tr>{% endfor %}</table>
    {% else %}<p class="muted">No reports generated yet.</p>{% endif %}</div>"""
    return page("Reports", body, rows=rows, role=session["role"], current_month=datetime.now().strftime("%Y-%m"))


@app.post("/reports/generate")
@roles_required("Reviewer", "Approver", "Administrator")
def generate_report():
    period = request.form.get("period", "")
    try:
        start = datetime.strptime(period, "%Y-%m")
    except ValueError:
        abort(400)
    end = datetime(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    start_iso = start.isoformat()
    end_iso = end.isoformat()
    with connect() as db:
        rows = db.execute("SELECT * FROM audit_events WHERE event_time >= ? AND event_time < ? ORDER BY event_time",
                           (start_iso, end_iso)).fetchall()
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM reports WHERE period=?", (period,)).fetchone()[0]
        report_id = db.execute("INSERT INTO reports(period,version,created_at,created_by,event_count,file_path,sha256,status) VALUES(?,?,?,?,?,?,?,?)",
                               (period, version, datetime.now(timezone.utc).isoformat(), session["user"],
                                len(rows), "", "", "Pending")).lastrowid
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Event ID","Event Time UTC","Server","Database","Login","Effective User","Application","Client Host","Schema","Object","Operation","Outcome","SQL Statement"])
        for row in rows:
            writer.writerow([row[k] for k in ("id","event_time","server_name","database_name","login_name","effective_user","application_name","client_host","schema_name","object_name","operation","outcome","statement")])
        filename = f"audit-{period}-v{version:03}.csv"
        file_path = REPORT_DIR / filename
        data = output.getvalue().encode("utf-8-sig")
        file_path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        db.execute("UPDATE reports SET file_path=?,sha256=? WHERE id=?", (str(file_path), digest, report_id))
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
    return page("Access denied", "<div class='card'><h1>Access denied</h1><p>Your demo role cannot perform this action.</p></div>"), 403


@app.errorhandler(409)
def conflict(_error):
    return page("Already reviewed", "<div class='card'><h1>Report already reviewed</h1><p>Generate a new version if it needs another review.</p></div>"), 409


initialize()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
