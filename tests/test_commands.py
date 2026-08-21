from xmpp_transport_telegram.core.commands import HELP_TEXT, command_response


def test_help_command_returns_command_list():
    assert command_response("/help") == HELP_TEXT


def test_empty_message_returns_help():
    assert command_response("   ") == HELP_TEXT


def test_status_is_disconnected_dummy_response():
    assert command_response("/status") == "Telegram account is not connected."


def test_known_placeholder_command():
    assert "not implemented yet" in command_response("/login +15551234567")


def test_unknown_command_includes_help():
    response = command_response("/unknown")

    assert response.startswith("Unknown command.")
    assert HELP_TEXT in response
