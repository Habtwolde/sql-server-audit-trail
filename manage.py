"""Create local app accounts without placing passwords in command history."""
import argparse
import getpass
from werkzeug.security import generate_password_hash
from storage import connect, initialize_store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create-user"])
    parser.add_argument("username")
    parser.add_argument("--role", choices=["Viewer", "Reviewer", "Approver", "Administrator"], required=True)
    args = parser.parse_args()
    username = args.username.strip().lower()
    if not username:
        parser.error("Username cannot be empty")
    password = getpass.getpass("Password (at least 12 characters): ")
    if len(password) < 12 or password != getpass.getpass("Confirm password: "):
        parser.error("Passwords must match and contain at least 12 characters")
    initialize_store()
    with connect() as db:
        if db.execute("SELECT 1 FROM app_users WHERE username=?", (username,)).fetchone():
            parser.error("Account already exists")
        db.execute("INSERT INTO app_users VALUES(?,?,?)", (username, generate_password_hash(password), args.role))
    print("Created application account:", username)


if __name__ == "__main__":
    main()
