# SQL Server Audit Trail

A web application for collecting SQL Server DML and DDL audit events, generating monthly CSV reports, and supporting review and approval.

## Current scope

- Continuously collect DML and DDL activity, including INSERT, UPDATE, DELETE, CREATE, ALTER/MODIFY, DROP, and TRUNCATE.
- Capture the SQL statement, executing identity, account, event time, and available server, database, schema, object, application, client host, and outcome details.
- Run collection as a background process using a dedicated database identity.
- Store collected audit events in a separate audit database.
- Generate monthly CSV reports and retain report history in configured storage, including Amazon S3.
- Allow authorized users to review and approve or reject reports, with decisions recorded against the report version.
- Support Windows-authenticated application access and deployment on an on-premises server or AWS EC2.
- Avoid DML and DDL triggers.

## Design status

The application design can proceed before selecting a SQL Server version or edition. The SQL Server capture adapter and its permissions will be finalized and validated against the target environment before production use.

Java and Streamlit are candidate implementation options. The web interface, collector, report generation, and approval workflow are still being designed.

## Initial validation scenarios

Use dummy users and test events to check:

- DML and DDL capture, including ALTER/MODIFY.
- Attribution of login/account, identity, timestamp, server, and database.
- Fast statements, concurrent activity, stored procedures, and dynamic SQL.
- Failed statements, rollbacks, long SQL text, and duplicate prevention.
- Collector restart and recovery after a connection outage.
- Monthly CSV generation, report version history, and review/approval decisions.

Capture completeness must be demonstrated against the target SQL Server configuration; polling frequency alone does not establish that no events were missed.
