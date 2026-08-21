# Task Notes: XMPP to Telegram Transport

## Project Map

- Telegram transport monorepo: `/home/ilya.basyrov/Projects/xmpp-transport-telegram`
- Telegram transport backend: `/home/ilya.basyrov/Projects/xmpp-transport-telegram/xmpp_transport_telegram`
- Telegram server module: `/home/ilya.basyrov/Projects/module-transport-telegram`
- MAX transport reference: `/home/ilya.basyrov/Projects/xmpp-transport-max`
- MAX module reference: `/home/ilya.basyrov/Projects/module-transport-max`
- Main panel project: `/home/ilya.basyrov/Projects/xabber-server-panel`
- Legacy Xabber Web: `/home/ilya.basyrov/Projects/xabber-server-panel/modules/xabber_web/`
- Xabber Web NG: `/home/ilya.basyrov/Projects/xabber-web-ng`
- XMPP server sources: `/home/ilya.basyrov/Projects/xabber-xmpp-server`
- Xabber protocol knowledge/custom XEP drafts: `/home/ilya.basyrov/Projects/xabber-knowledge`
- Compiled local XMPP server: `/home/ilya.basyrov/Projects/xabber-server-panel/xmpp`

## Development Rules

- Treat the paths above as references. Do not inspect other projects proactively;
  open them only when a task needs that context.
- Keep the Python transport compatible with Python 3.9. Do not use Python
  3.10-only typing syntax such as `A | B`; use `Optional[...]` or `Union[...]`.
- Normal source changes for the Telegram transport backend belong in this repo.
- Normal source changes for the Telegram server module belong in
  `/home/ilya.basyrov/Projects/module-transport-telegram`.
- Do not modify `xabber-server-panel`, `xabber-web-ng`, legacy `xabber_web`,
  `xabber-xmpp-server`, or the compiled `xmpp` tree as source work.
- The only exception is temporary diagnostic instrumentation or deploying a
  rebuilt local module into the compiled server for live testing.
- Remove temporary diagnostics after investigation unless explicitly asked to
  keep them.
- Prefer narrow diagnostics and filtered logs. Do not dump large logs, full
  stanzas, media URLs, generated bundles, Telegram auth codes, session strings,
  or message bodies into chat context.

## Architecture

- `xmpp_transport_telegram/` is the external XMPP component and owns Telegram MTProto access,
  session state, XMPP stanza/IQ translation, metadata sync, media references,
  deduplication, and loop suppression.
- `module-transport-telegram` is a privileged roster push helper for virtual
  Telegram contacts.
- Runtime state lives in PostgreSQL: XMPP accounts, encrypted Telegram sessions,
  auth attempts, roster/group sync signatures, and transport-owned mappings
  where needed.
- The transport must not write directly to Xabber Server database tables.
- Server-owned behavior should be reached through normal XMPP routing,
  XEP-GROUPS IQ/stanza flows, exported client-visible protocol paths, hooks, or
  documented server APIs.
- Custom XEP details must be taken from `/home/ilya.basyrov/Projects/xabber-knowledge`
  before designing protocol payloads.

## Dependency Decision

- Use `Telethon` for Telegram MTProto personal-account access.
- Keep `slixmpp`, `aiohttp`, `asyncpg`, and `cryptography` from the MAX backend
  shape.
- Add `pillow` only when avatar/image processing is actually implemented.
- Do not use Pyrogram for the initial backend because its official documentation
  says it is no longer maintained or supported.

References checked:

- Telethon docs: https://docs.telethon.dev/en/stable/
- Telethon client reference: https://docs.telethon.dev/en/stable/modules/client.html
- Telethon asyncio docs: https://docs.telethon.dev/en/stable/concepts/asyncio.html
- Pyrogram docs: https://docs.pyrogram.org/

## Module Boundary

- Use the module only for:
  - `add-roster-contact`
  - `rename-roster-contact`
  - `remove-roster-contact`
- Do not add module operations for groups, members, avatars, archives, message
  delivery, fanout, permissions, carbons, media, or conversation metadata.
- If a feature cannot be expressed by the component through supported
  XMPP/server protocol, document the integration gap before adding server-side
  code.

## Runtime Shape

- Target approach: XMPP external component via `ejabberd_service`.
- Component domain example: `telegram.example.com`.
- Component local port: `127.0.0.1:5238`.
- User control contact: `bot@telegram.example.com`.
- Direct Telegram dialogs are represented as
  `chat-<telegram_peer_id>@<component-domain>`.
- Telegram groups/channels should be represented through normal Xabber
  groups/protocol surfaces where possible, with transport-side mapping and loop
  suppression.

## Useful Verification Commands

```bash
cd /home/ilya.basyrov/Projects/xmpp-transport-telegram
venv/bin/python -m compileall -q xmpp_transport_telegram
```

```bash
cd /home/ilya.basyrov/Projects/xmpp-transport-telegram
docker compose build transport
docker compose up -d --force-recreate transport
curl -sS http://127.0.0.1:8089/health
```

```bash
cd /home/ilya.basyrov/Projects/module-transport-telegram
make
/home/ilya.basyrov/Projects/xabber-server-panel/xmpp/bin/ejabberdctl restart_module example.com mod_transport_telegram
```

## Known Production Work

- Replace XMPP-message Telegram code/password input with short-lived HTTPS
  submission forms.
- Implement persistent encrypted Telethon session storage.
- Implement dialog snapshot sync and roster push signatures.
- Define group/channel mapping after reading Xabber custom XEP drafts.
- Extend media/file bridging after inbound/outbound text behavior is stable.
