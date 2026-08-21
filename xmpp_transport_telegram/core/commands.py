HELP_TEXT = """Commands:
/login <phone>
/code <code>
/password <password>
/status
/contacts
/add <number>
/logout
/help"""


def command_response(body: str) -> str:
    text = body.strip()
    command = text.split(None, 1)[0].lower() if text else "/help"

    if command == "/help":
        return HELP_TEXT
    if command == "/login":
        return "Telegram login is not implemented yet. Dummy command received."
    if command == "/code":
        return "Telegram code handling is not implemented yet. Dummy command received."
    if command == "/password":
        return "Telegram password handling is not implemented yet. Dummy command received."
    if command == "/status":
        return "Telegram account is not connected."
    if command == "/contacts":
        return "Telegram contact sync is not implemented yet."
    if command == "/add":
        return "Telegram contact add is not implemented yet. Dummy command received."
    if command == "/logout":
        return "Telegram logout is not implemented yet. Dummy command received."
    return "Unknown command.\n\n%s" % HELP_TEXT
