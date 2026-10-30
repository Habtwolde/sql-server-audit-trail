# CSV report storage on PC, on-premises or EC2

## Local history

Local storage is the default and requires no AWS account:

```dotenv
REPORT_ARCHIVE_BACKEND=local
```

The default folder is instance/reports/live inside the application directory.
To choose another folder on your PC or application server:

```dotenv
AUDIT_REPORT_DIR='C:\SqlAuditReports'
```

On a Linux host/container, use an absolute mounted path such as /app/instance/reports/live.
The application identity needs write access. Set the same folder in the web and
collector processes and restart both after changing configuration. Paths are on
the APPLICATION host, separate from the SQL Server Audit .sqlaudit directory.

Changing AUDIT_REPORT_DIR affects newly generated CSVs. Existing report records
continue to use their recorded original paths; the app does not move/delete old
files. Keep the old directory accessible, or perform an explicit migration later.
The SQLite repository and report folders must be on persistent storage shared by
the web and collector on a single host.

Verify without AWS or SQL Server access:

```powershell
python report_archive.py --status
```

Generate a report in Reports and download its CSV. The history row shows Local.
Downloads verify the CSV against its stored SHA-256 digest. A modified local file
is rejected instead of being served as the original report.

## Optional S3 history

Configure only when the destination bucket and AWS identity are ready:

```dotenv
REPORT_ARCHIVE_BACKEND=s3
REPORT_S3_BUCKET=your-existing-private-bucket
REPORT_S3_PREFIX=sql-server-audit-trail/production
REPORT_S3_ENCRYPTION=AES256
AWS_DEFAULT_REGION=us-east-1
```

Choose your bucket's actual region and a unique prefix for each PC/test/production
deployment. Existing buckets are used; the app does not create buckets, policies,
IAM identities, or public access. Use the bucket's public-access blocking settings.
For customer-managed KMS encryption, set:

```dotenv
REPORT_S3_ENCRYPTION=aws:kms
REPORT_S3_KMS_KEY_ID=your-approved-key-id-or-arn
```

Use an EC2 instance role when hosted on EC2. On a PC or on-premises host, use an
approved AWS profile or other standard SDK credential provider, for example
AWS_PROFILE=sql-audit. Never commit credentials or send them in screenshots/chat.
A headless host needs renewable credentials that remain usable without an
interactive sign-in; local SSO profile sessions can expire.

The archive identity needs s3:PutObject, s3:GetObject and s3:GetObjectVersion on the selected prefix.
An example policy is in s3-policy.example.json. KMS configurations also require
appropriate kms:GenerateDataKey and kms:Decrypt permissions in IAM/key policies.
The application does not need bucket listing or object deletion for archiving.
Administrative bucket setup is separate from runtime permissions.

Run with the activated Python environment:

```powershell
python -m pip install -r requirements.txt
python report_archive.py --status
python report_archive.py --sync
```

--status validates configuration and shows queue counts without contacting AWS.
--sync queues all unarchived report versions, including existing history, and
uploads at most 20 per call. Inspect Reports > Report storage and each report's
Archive column. Pending/Error uploads retry in the continuous collector; no Task
Scheduler or additional scheduling process is required. Manual reports are queued
on the next archive cycle. Local mode makes no AWS requests.

Object keys follow this pattern:

```text
<prefix>/live/<year>/<month>/audit-<period>-v<version>-<sha256>.csv
```

Uploads use explicit server-side encryption and a SHA-256 transport checksum.
Conditional writes avoid overwriting an existing key. If an earlier upload
succeeded but its response was lost, a retry reads and hashes the actual S3 bytes
before accepting the existing copy. An existing object with different bytes is
reported as an error, not overwritten.

Each queued destination (bucket, key, encryption and KMS key) is persisted.
Changing configuration applies to newly queued reports; already queued history
keeps its original destination, preventing silent migration. Switching to local
mode pauses outstanding uploads without deleting their history.

An Uploaded status is a record of a successful upload, not a periodic check that
the object still exists. If a local file is missing, the authenticated CSV
download can retrieve the recorded S3 version and verify its hash. The app retains
all local CSVs automatically; there is no local cleanup policy in this release.
S3 version IDs are recorded when the bucket supplies them. S3 Versioning/Object
Lock retention policies remain optional bucket administration choices, not
features enabled by this application.

Only the CSV snapshot is archived. Approval decisions, users, source evidence,
scheduler state and report metadata remain in the application database, which
requires its own backup. Approving/rejecting a report does not modify its CSV or
rewrite its archived copy.

## Validation boundary

Automated tests use mocked S3 responses and the official SDK request model for
upload, retry, collision handling, checksums and verified download behavior.
A real bucket upload/download and the deployment identity's permissions still
need validation in your PC/on-premises/EC2 environment.
