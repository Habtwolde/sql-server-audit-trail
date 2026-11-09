"""Run real SQL Server integration tests in a disposable database.

Run: python tests/run_sqlserver.py
Uses .env AUDIT_STORE_CONNECTION_STRING. Requires CREATE/DROP DATABASE permission.
Never deletes or writes application rows in the configured database.
"""
import os
from pathlib import Path
import re
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
import pyodbc
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
connection_string = os.environ.get("AUDIT_STORE_CONNECTION_STRING", "").strip()
if not connection_string:
    raise SystemExit("Set AUDIT_STORE_CONNECTION_STRING in .env first")
name = "SqlAuditTrail_test_" + uuid.uuid4().hex
# Only a simple explicit DATABASE value is accepted for this development helper.
pattern = r"(?i)(^|;)DATABASE=[^;]*"
if not re.search(pattern, connection_string):
    raise SystemExit("Test connection must contain an explicit DATABASE setting")
test_connection = re.sub(pattern, lambda m: m[1] + "DATABASE=" + name, connection_string)
admin = pyodbc.connect(connection_string, autocommit=True, timeout=15)
created = False
try:
    admin.execute(f"CREATE DATABASE [{name}]")
    created = True
    probe = pyodbc.connect(test_connection, autocommit=True, timeout=15)
    try:
        if probe.execute("SELECT DB_NAME()").fetchone()[0] != name:
            raise RuntimeError("Refusing to test outside the disposable database")
    finally:
        probe.close()
    os.environ["AUDIT_TEST_STORE_CONNECTION_STRING"] = test_connection
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_live.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
finally:
    # Name generated locally, never taken from configuration or command input.
    if created:
        admin.execute(f"ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        admin.execute(f"DROP DATABASE [{name}]")
    admin.close()
sys.exit(not result.wasSuccessful())
