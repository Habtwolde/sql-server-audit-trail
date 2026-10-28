# SQL Server Audit Trail

A first local prototype for exploring continuous SQL Server DML/DDL audit reporting, monthly CSV generation, and report review.

## Run locally

Requires Python 3.10 or newer.

```bash
python -m venv .venv
```

Activate the environment, then install and start the app:

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. The application creates a local SQLite database and seeds sample events on first launch. It does not connect to SQL Server.

Demo identities are available from the sign-in page:

- `demo.viewer` — view the audit trail and reports.
- `demo.reviewer` — generate reports and review reports created by someone else.
- `demo.approver` — same prototype permissions as reviewer.
- `demo.admin` — same prototype permissions as reviewer.

The login is intentionally a role selector without passwords. It is only for local demonstration and must not be exposed as a real authentication system.

## Run in a container

```bash
docker build -t sql-server-audit-trail .
docker run --rm -p 5000:5000 -v audit-data:/app/instance sql-server-audit-trail
```

For any shared deployment, set a strong `FLASK_SECRET_KEY`, use HTTPS behind a production web server or load balancer, and restrict network access. The built-in Flask development server is for local demonstration only.

## Prototype features

- Sample audit trail with DML and DDL examples, including ALTER/MODIFY, MERGE, and TRUNCATE.
- Event list and operation filter.
- Monthly CSV generation with a version number and SHA-256 digest.
- Review/approval or rejection recorded with reviewer, decision, comment, and time.
- Local report history in `instance/reports`.

## Agreed product scope

- Continuously capture SQL Server DML and DDL events, including INSERT, UPDATE, DELETE, CREATE, ALTER/MODIFY, DROP, and TRUNCATE.
- Record SQL text, executing identity, account, event time, and available server, database, schema, object, application, client host, and outcome details.
- Collect in a background process using a dedicated identity and store events in a separate audit database.
- Generate monthly CSV reports and retain report history in configured storage, including S3.
- Support Windows-authenticated access, with review and approval roles.
- Deploy on a PC for development, an on-premises server, or AWS EC2.
- Do not use DML or DDL triggers.

## Next implementation stages

1. Confirm the audit event schema and report format against the existing report example.
2. Add a collector interface and simulated polling source.
3. Implement durable collection checkpoints and missed-event monitoring.
4. Select and validate the SQL Server capture method and minimum permissions for the target version/edition.
5. Replace demo sign-in with Windows/organizational authentication and map identities to application roles.
6. Add production storage options, including S3, and deployment configuration.
7. Validate event coverage and recovery using dummy users and SQL scenarios.

The current version is a workflow prototype. It does not yet capture SQL Server activity, use Windows authentication, store events in a separate database, or upload to S3. Polling frequency alone cannot guarantee complete capture; completeness must be proven against the target SQL Server audit configuration.
