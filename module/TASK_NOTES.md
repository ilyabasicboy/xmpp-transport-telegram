# Task Notes: module-transport-telegram

## Purpose

`mod_transport_telegram` is a small Xabber Server module for the external
`xmpp-transport-telegram` component.

Its only product responsibility is privileged roster push for virtual Telegram
contacts. The module exists because an external XEP-0114 component cannot
directly manage a user's roster.

## Project Boundaries

- Module project: `/home/ilya.basyrov/Projects/xmpp-transport-telegram/module`
- Transport backend: `/home/ilya.basyrov/Projects/xmpp-transport-telegram/transport`
- Main panel project: `/home/ilya.basyrov/Projects/xabber-server-panel`
- Legacy Xabber Web: `/home/ilya.basyrov/Projects/xabber-server-panel/modules/xabber_web/`
- Xabber Web NG: `/home/ilya.basyrov/Projects/xabber-web-ng`
- XMPP server sources: `/home/ilya.basyrov/Projects/xabber-xmpp-server`
- Compiled local XMPP server: `/home/ilya.basyrov/Projects/xabber-server-panel/xmpp`

Normal source changes for this module belong in this project. Do not modify
Xabber Server sources, Xabber Web projects, or the compiled server tree as
source work. Deploying a rebuilt `.beam` into the compiled server is allowed
only as a local runtime/test action.

Do not commit generated `.beam` files or archives unless explicitly asked for
release artifacts.

## Supported API

Namespace:

```text
urn:xabber:transport:telegram:1
```

Supported operations:

- `add-roster-contact`
- `rename-roster-contact`
- `remove-roster-contact`

No other transport operations belong in the module by default.

## Implementation Rules

- Keep the module minimal and roster-only.
- Use existing Xabber Server APIs, especially `mod_roster`, instead of direct SQL.
- Do not write directly to roster, groups, archive, avatar, permissions,
  sessions, conversation, or other server tables.
- Validate that incoming IQ requests come from configured `allowed_components`.
- `allowed_components` must be configuration-owned and fail closed when absent.
- Validate that `owner_jid` belongs to the destination local virtual host.
- Validate that `contact_jid` belongs to an allowed component domain.
- Accept only bare owner/contact JIDs for roster operations.
- Make roster operations idempotent where possible.
- Use the server logger from `logger.hrl` macros, not `error_logger`.
- Keep temporary diagnostic logs narrow and remove them before finishing a task.

## What Does Not Belong Here

Do not implement or reintroduce:

- group creation/update;
- group member synchronization;
- group message delivery or fanout;
- direct or group self-message delivery;
- archive/MAM writes;
- carbons;
- stanza-id generation;
- avatar storage or avatar protocol workarounds;
- media storage, proxying, or file-sharing translation;
- transport-owned deduplication or loop suppression;
- wrappers around XEP-GROUPS client/server behavior.

These responsibilities belong in `xmpp-transport-telegram` or in existing
Xabber Server protocol paths. If the component cannot perform a required
operation via supported XMPP/server protocol, document the integration gap
before expanding this module.
