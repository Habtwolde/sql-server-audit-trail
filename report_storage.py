"""Local CSV report access for the on-premises application server."""
import argparse
import hashlib
import json
from pathlib import Path

from storage import REPORT_DIR


def report_bytes(report):
    """Return the recorded local file only after checking its original digest."""
    data = Path(report["file_path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != report["sha256"]:
        raise ValueError("Local CSV checksum mismatch")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", required=True,
                        help="Show the configured local report directory")
    parser.parse_args()
    print(json.dumps({"storage": "local", "local_report_directory": str(REPORT_DIR)}))


if __name__ == "__main__":
    main()
