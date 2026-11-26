"""Account commands of the command line interface, run by the operator (`admin`).

There is no self-service and no e-mail: the admin creates accounts and resets passwords here.
Passwords are read interactively (twice) or, for scripts, from standard input.
"""

import argparse
import getpass
import sys
from collections.abc import Callable

from sqlmodel import Session

from app.core.db import engine, init_db
from app.core.users import Role, UserError, create_user, list_users, reset_password

COMMANDS = frozenset({"user-create", "user-reset-password", "user-list"})

PasswordReader = Callable[[], str]


def read_password_interactive() -> str:
    first = getpass.getpass("Neues Passwort: ")
    second = getpass.getpass("Passwort wiederholen: ")
    if first != second:
        raise UserError("Die Passwörter stimmen nicht überein")
    return first


def read_password_stdin() -> str:
    return sys.stdin.readline().rstrip("\r\n")


def main(argv: list[str], read_password: PasswordReader | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Benutzerverwaltung")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("user-create", help="Benutzer anlegen")
    create.add_argument("username")
    create.add_argument("--role", choices=[r.value for r in Role], default=Role.EDITOR.value)
    create.add_argument("--password-stdin", action="store_true", help="Passwort von stdin lesen")
    reset = commands.add_parser(
        "user-reset-password", help="Passwort neu setzen (beendet alle Sitzungen, hebt Sperre auf)"
    )
    reset.add_argument("username")
    reset.add_argument("--password-stdin", action="store_true", help="Passwort von stdin lesen")
    commands.add_parser("user-list", help="Benutzer mit Rolle auflisten")
    args = parser.parse_args(argv)

    init_db(engine)
    with Session(engine) as session:
        try:
            if args.command == "user-list":
                for user in list_users(session):
                    state = "" if user.active else " (deaktiviert)"
                    print(f"{user.username}\t{user.role.value}{state}")
                return 0
            reader = read_password or (
                read_password_stdin if args.password_stdin else read_password_interactive
            )
            password = reader()
            if args.command == "user-create":
                user = create_user(session, args.username, password, Role(args.role))
                print(f"Benutzer „{user.username}“ ({user.role.value}) angelegt.")
            else:
                user = reset_password(session, args.username, password)
                print(f"Passwort für „{user.username}“ neu gesetzt; alle Sitzungen beendet.")
        except UserError as error:
            print(error, file=sys.stderr)
            return 1
    return 0
