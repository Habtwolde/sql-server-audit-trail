"""Optional S3 CSV history, retried by the existing collector process."""
import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from storage import MODE, REPORT_DIR, connect, initialize_store


def archive_settings():
    backend = os.environ.get("REPORT_ARCHIVE_BACKEND", "local").lower()
    if backend == "local":
        return None
    if backend != "s3":
        raise ValueError("REPORT_ARCHIVE_BACKEND must be local or s3")
    bucket = os.environ.get("REPORT_S3_BUCKET", "").strip()
    prefix = os.environ.get("REPORT_S3_PREFIX", "").strip("/")
    encryption = os.environ.get("REPORT_S3_ENCRYPTION", "AES256")
    kms_key = os.environ.get("REPORT_S3_KMS_KEY_ID", "").strip()
    if not bucket or "/" in bucket or not prefix or any(ord(c) < 32 for c in bucket + prefix):
        raise ValueError("Set a bucket and deployment-specific S3 prefix")
    if encryption not in ("AES256", "aws:kms") or (encryption == "aws:kms" and not kms_key):
        raise ValueError("Invalid S3 encryption configuration")
    if MODE != "live":
        raise ValueError("S3 archiving requires live mode")
    return dict(bucket=bucket, prefix=prefix, encryption=encryption,
                kms_key=kms_key if encryption == "aws:kms" else "")


def s3_client():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", config=Config(connect_timeout=10, read_timeout=30,
                         retries={"max_attempts": 3, "mode": "standard"}))


def queue_archives(settings):
    """Persist destinations for all unqueued report history, including old versions."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT r.* FROM reports r LEFT JOIN report_archives a "
                          "ON a.report_id=r.id WHERE a.report_id IS NULL").fetchall()
        for row in rows:
            year, month = row["period"].split("-")
            key = (f"{settings['prefix']}/{MODE}/{year}/{month}/"
                   f"audit-{row['period']}-v{row['version']:03}-{row['sha256']}.csv")
            if len(key.encode("utf-8")) > 1024:
                raise ValueError("S3 object key too long")
            db.execute("INSERT INTO report_archives(report_id,bucket,object_key,encryption,kms_key,status) "
                       "VALUES(?,?,?,?,?,'Pending')",
                       (row["id"], settings["bucket"], key, settings["encryption"], settings["kms_key"]))


def read_s3(client, archive, expected_digest):
    args = {"Bucket": archive["bucket"], "Key": archive["object_key"]}
    if archive["version_id"]:
        args["VersionId"] = archive["version_id"]
    result = client.get_object(**args)
    body = result["Body"]
    try:
        data = body.read()
    finally:
        body.close()
    if hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError("Archived CSV checksum mismatch")
    return data, result.get("VersionId", "")


def upload_report(client, row):
    data = Path(row["file_path"]).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != row["sha256"]:
        raise ValueError("Local CSV checksum mismatch")
    args = dict(Bucket=row["bucket"], Key=row["object_key"], Body=data,
                ContentType="text/csv; charset=utf-8", IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
                ServerSideEncryption=row["encryption"], Metadata={"sha256": digest})
    if row["encryption"] == "aws:kms":
        args["SSEKMSKeyId"] = row["kms_key"]
    try:
        result = client.put_object(**args)
        return result.get("VersionId", "")
    except Exception as error:
        # A previous attempt may have uploaded successfully but lost its response.
        # Compare actual remote bytes before accepting the existing object.
        if str(getattr(error, "response", {}).get("Error", {}).get("Code")) not in ("PreconditionFailed", "412"):
            raise
        _, version = read_s3(client, row, digest)
        return version


def set_state(status, detail, now):
    with connect() as db:
        db.execute("INSERT INTO report_archive_state(id,status,last_attempt,detail) VALUES(1,?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET status=excluded.status,"
                   "last_attempt=excluded.last_attempt,detail=excluded.detail",
                   (status, now, detail))


def sync_archives(client=None):
    """Bound work per cycle; errors preserve local CSVs and retryable queue rows."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        settings = archive_settings()
        if settings is None:
            return {"status": "Local", "uploaded": 0, "failed": 0}
        queue_archives(settings)
        with connect() as db:
            rows = db.execute("SELECT r.*, a.bucket,a.object_key,a.encryption,a.kms_key,a.version_id "
                              "FROM reports r JOIN report_archives a ON a.report_id=r.id "
                              "WHERE a.status != 'Uploaded' ORDER BY a.last_attempt, r.id LIMIT 20").fetchall()
        if not rows:
            set_state("Ready", "No pending CSV uploads.", now)
            return {"status": "Ready", "uploaded": 0, "failed": 0}
        client = client if client is not None else s3_client()
        uploaded = failed = 0
        for row in rows:
            attempt = datetime.now(timezone.utc).isoformat()
            try:
                version = upload_report(client, row)
                with connect() as db:
                    db.execute("UPDATE report_archives SET status='Uploaded',last_attempt=?,"
                               "archived_at=?,version_id=?,error='' WHERE report_id=?",
                               (attempt, attempt, version, row["id"]))
                uploaded += 1
            except Exception as error:
                detail = f"Upload failed ({type(error).__name__}); verify credentials, permissions, storage and checksum."
                with connect() as db:
                    db.execute("UPDATE report_archives SET status='Error',last_attempt=?,error=? "
                               "WHERE report_id=? AND status != 'Uploaded'", (attempt, detail, row["id"]))
                failed += 1
        status = "Error" if failed else "Ready"
        set_state(status, f"Uploaded {uploaded}; failed {failed} this cycle. Local CSVs retained.", now)
        return {"status": status, "uploaded": uploaded, "failed": failed}
    except Exception as error:
        detail = f"Archiving failed ({type(error).__name__}); check S3 configuration, credentials and storage."
        set_state("Error", detail, now)
        return {"status": "Error", "uploaded": 0, "failed": 0, "detail": detail}


def report_bytes(report, client=None):
    path = Path(report["file_path"])
    if path.is_file():
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != report["sha256"]:
            raise ValueError("Local CSV checksum mismatch")
        return data
    with connect() as db:
        archive = db.execute("SELECT * FROM report_archives WHERE report_id=? AND status='Uploaded'",
                             (report["id"],)).fetchone()
    if archive is None:
        raise FileNotFoundError("No report copy available")
    data, _ = read_s3(client if client is not None else s3_client(), archive, report["sha256"])
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show configuration and queue counts without AWS access")
    group.add_argument("--sync", action="store_true", help="Queue report history and upload up to 20 pending CSVs")
    args = parser.parse_args()
    initialize_store()
    if args.sync:
        result = sync_archives()
        print(json.dumps(result))
        if result["status"] == "Error":
            raise SystemExit(1)
        return
    try:
        settings = archive_settings()
        with connect() as db:
            counts = {r[0]: r[1] for r in db.execute(
                "SELECT status,count(*) FROM report_archives GROUP BY status")}
        print(json.dumps({"backend": "s3" if settings else "local", "local_report_directory": str(REPORT_DIR),
                          "bucket": settings["bucket"] if settings else None,
                          "prefix": settings["prefix"] if settings else None, "queue": counts}))
    except Exception as error:
        print(f"Archive configuration invalid ({type(error).__name__}).")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
