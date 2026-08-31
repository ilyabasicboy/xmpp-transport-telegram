# xmpp-transport-telegram

External XMPP component transport for personal Telegram accounts.

The project follows the MAX transport shape: a Python XEP-0114 transport
backend. The backend owns Telegram MTProto access, session state, stanza
translation, metadata sync, media references, deduplication, and loop
suppression.

The companion Xabber Server module lives in a separate project:

```text
/home/ilya.basyrov/Projects/module-transport-telegram
```

## Layout

```text
xmpp_transport_telegram/  Python XEP-0114 component backend
```

## Dependency Choice

Use Telethon for Telegram MTProto user-account access. Telethon is asynchronous,
Python-native, and its current documentation describes `TelegramClient` as the
central client for connecting, receiving updates, and sending messages.

Do not start with Pyrogram: its official documentation currently says the
project is no longer maintained or supported.

Primary references:

- https://docs.telethon.dev/en/stable/
- https://docs.telethon.dev/en/stable/modules/client.html
- https://docs.telethon.dev/en/stable/concepts/asyncio.html
- https://docs.pyrogram.org/

## Configure Xabber Server

Use matching panel settings once the module is implemented:

```python
XMPP_COMPONENT_TELEGRAM_ENABLED = True
XMPP_COMPONENT_TELEGRAM_PORT = '5238'
XMPP_COMPONENT_TELEGRAM_IP = '127.0.0.1'
XMPP_COMPONENT_TELEGRAM_HOST = 'telegram.example.com'
XMPP_COMPONENT_TELEGRAM_PASSWORD = 'long-random-secret'
```

Install and enable `module-transport-telegram` for the same host. The module
must allow the transport component domain:

```yaml
allowed_components:
  - "telegram.example.com"
```

Direct Telegram dialogs should initially be represented as:

```text
chat-<telegram_peer_id>@telegram.example.com
```

The command contact is:

```text
bot@telegram.example.com
```

## Install Backend

```bash
cd xmpp-transport-telegram
virtualenv venv -p python3
venv/bin/pip install -r requirements.txt
cp config.ini.example config.ini
```

Create a PostgreSQL database and user, then set `database.url`.

Generate the encryption key used for Telegram session data:

```bash
venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `telegram.api_id` and `telegram.api_hash` from Telegram's official app
configuration flow at https://my.telegram.org/apps.

## Run Backend

```bash
venv/bin/python -m xmpp_transport_telegram --config config.ini
```

The process creates its PostgreSQL tables on startup and exposes:

- `GET /health`
- `GET /qr/<file>.svg`

The transport stores Telegram login QR SVG files under `server.qr_storage_dir`.
Set `server.qr_base_url` to the externally reachable base URL of this
transport's HTTP listener; `/qr` is added automatically.

## User Flow

Command contact commands:

```text
/login
/password <password>
/status
/contacts
/add <number>
/logout
/help
```

Telegram user authorization starts with a Telethon QR login token. Send `/login`
to `bot@telegram.example.com`, then scan the returned SVG QR from a device
already signed in to Telegram before it expires. If Telegram requires a cloud
password after accepting the login, send `/password <password>`.

Production work should replace `/password` chat commands with a short-lived
HTTPS form so secrets do not remain in XMPP history.

## Boundaries

- The Python transport must not write directly to Xabber Server database tables.
- The server module is roster-only: add, rename, and remove virtual Telegram
  contacts.
- Groups, members, messages, archives, avatars, media, fanout, and loop
  suppression belong in the backend or existing Xabber protocol paths.
- Custom XEP details must be taken from the shared Xabber knowledge project
  before designing protocol payloads.
