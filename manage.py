"""Command-line account recovery for the dashboard.

Sign-in is the one thing that can't be fixed from inside the dashboard, so it
gets a way in from the server shell. Run these on the box the site is deployed
to, from the project folder:

    python manage.py users                       # who can sign in
    python manage.py reset smashteam             # prompt for a new password
    python manage.py reset smashteam --generate  # print a strong one instead
    python manage.py create newname --role developer
    python manage.py delete oldname

`reset` is the answer to "the seeded password stopped working": seeding only
ever runs for a username that doesn't exist yet, so once the password has been
changed from Account → Change password, the value in db.py is dead and only
this can replace it.

Passwords are never echoed back into the store — only the hash is written.
"""

import argparse
import getpass
import sys

from dotenv import load_dotenv

load_dotenv()

import db  # noqa: E402  (must follow load_dotenv, same as app.py)


def _app():
    """The real app, imported lazily so `users` doesn't pay for template setup."""
    from app import app
    return app


def _resolve(username):
    user = db.get_user_by_username(username)
    if user is None:
        known = ", ".join(u["username"] for u in db._all("users")) or "none"
        sys.exit("No account named %r. Known accounts: %s" % (username, known))
    return user


def _new_password(args):
    if args.generate:
        password = db.suggest_password()
        print("Generated password: %s" % password)
        return password

    password = getpass.getpass("New password: ")
    if len(password) < 10:
        sys.exit("Choose a password of at least 10 characters.")
    if password != getpass.getpass("Confirm password: "):
        sys.exit("The passwords don't match.")
    return password


def cmd_users(args):
    users = sorted(db._all("users"), key=lambda u: u["id"])
    if not users:
        print("No accounts exist. Restart the app to seed one.")
        return
    print("%-4s %-20s %-11s %-24s %s" % ("ID", "USERNAME", "ROLE", "DISPLAY NAME", "LAST SIGN-IN"))
    for user in users:
        print("%-4s %-20s %-11s %-24s %s" % (
            user["id"], user["username"], user["role"],
            user["display_name"] or "—", user["last_login_at"] or "never"))


def cmd_reset(args):
    user = _resolve(args.username)
    db.update_user(user["id"], password=_new_password(args))
    print("Password updated for '%s'. Sign in at /admin/login." % user["username"])


def cmd_create(args):
    if db.get_user_by_username(args.username) is not None:
        sys.exit("An account named %r already exists." % args.username)
    if args.role not in db.ROLES:
        sys.exit("Role must be one of: %s" % ", ".join(db.ROLES))
    db.create_user(args.username, _new_password(args),
                   display_name=args.display_name or args.username,
                   role=args.role)
    print("Created %s account '%s'." % (args.role, args.username))


def cmd_delete(args):
    user = _resolve(args.username)
    if db.count_users_by_role(db.ROLE_DEVELOPER) == 1 and user["role"] == db.ROLE_DEVELOPER:
        sys.exit("That is the only developer account — deleting it would lock "
                 "the dashboard out. Create another one first.")
    confirm = input("Delete '%s'? Posts and leads are kept. [y/N] " % user["username"])
    if confirm.strip().lower() not in ("y", "yes"):
        sys.exit("Cancelled.")
    db.delete_user(user["id"])
    print("Deleted '%s'." % user["username"])


def cmd_unlock(args):
    """Clear brute-force lockouts, e.g. after locking yourself out."""
    with db._mutate("security") as data:
        removed = len(data["records"])
        data["records"] = []
    print("Cleared %d sign-in lockout counter(s)." % removed)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("users", help="list dashboard accounts").set_defaults(fn=cmd_users)

    reset = subs.add_parser("reset", help="set a new password for an account")
    reset.add_argument("username")
    reset.add_argument("--generate", action="store_true",
                       help="generate and print a strong password")
    reset.set_defaults(fn=cmd_reset)

    create = subs.add_parser("create", help="add an account")
    create.add_argument("username")
    create.add_argument("--role", default=db.ROLE_CLIENT, choices=db.ROLES)
    create.add_argument("--display-name", default="")
    create.add_argument("--generate", action="store_true")
    create.set_defaults(fn=cmd_create)

    delete = subs.add_parser("delete", help="remove an account")
    delete.add_argument("username")
    delete.set_defaults(fn=cmd_delete)

    subs.add_parser("unlock", help="clear sign-in lockouts").set_defaults(fn=cmd_unlock)

    args = parser.parse_args()

    # init_db() resolves the store location and seeds the first login, so every
    # command sees the same data the running site does.
    db.init_db(_app())
    args.fn(args)


if __name__ == "__main__":
    main()
