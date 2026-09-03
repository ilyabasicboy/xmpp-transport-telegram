import logging
import os
import posixpath
import tempfile
from typing import List, Optional
from urllib.parse import unquote, urlsplit

import aiohttp
from telethon import TelegramClient
from telethon.errors import WebpageCurlFailedError, WebpageMediaEmptyError
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest

from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.models import (
    TelegramAvatar,
    TelegramContact,
    TelegramDialog,
    TelegramForwardReference,
)


log = logging.getLogger(__name__)

MAX_OUTGOING_MEDIA_BYTES = 50 * 1024 * 1024


class TelegramBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def client_for_session(self, session_data: Optional[str] = None) -> TelegramClient:
        return TelegramClient(
            StringSession(session_data or ""),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
            catch_up=False,
        )

    async def list_dialogs(self, client: TelegramClient, limit: int = 100) -> List[TelegramDialog]:
        dialogs = []
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            peer_id = int(dialog.id)
            title = dialog.name or str(peer_id)
            dialogs.append(
                TelegramDialog(
                    peer_id=peer_id,
                    title=title,
                    username=getattr(entity, "username", None),
                    phone=getattr(entity, "phone", None),
                    is_group=bool(getattr(dialog, "is_group", False)),
                    is_channel=bool(getattr(dialog, "is_channel", False)),
                )
            )
        return dialogs

    async def list_contacts(self, client: TelegramClient, include_avatars: bool = True) -> List[TelegramContact]:
        contacts_by_peer_id = {}
        result = await client(GetContactsRequest(hash=0))
        for user in getattr(result, "users", []):
            peer_id = int(user.id)
            avatar, avatar_photo_id, avatar_download_failed = await self.small_avatar(
                client,
                user,
                include_content=include_avatars,
            )
            contacts_by_peer_id[peer_id] = TelegramContact(
                peer_id=peer_id,
                title=self._user_title(user),
                username=getattr(user, "username", None),
                phone=getattr(user, "phone", None),
                avatar=avatar,
                avatar_photo_id=avatar_photo_id,
                avatar_download_failed=avatar_download_failed,
            )

        async for dialog in client.iter_dialogs():
            if bool(getattr(dialog, "is_group", False)) or bool(getattr(dialog, "is_channel", False)):
                continue
            entity = dialog.entity
            peer_id = int(dialog.id)
            if peer_id in contacts_by_peer_id:
                continue
            title = dialog.name or self._user_title(entity)
            avatar, avatar_photo_id, avatar_download_failed = await self.small_avatar(
                client,
                entity,
                include_content=include_avatars,
            )
            contacts_by_peer_id[peer_id] = TelegramContact(
                peer_id=peer_id,
                title=title,
                username=getattr(entity, "username", None),
                phone=getattr(entity, "phone", None),
                avatar=avatar,
                avatar_photo_id=avatar_photo_id,
                avatar_download_failed=avatar_download_failed,
            )
        return sorted(contacts_by_peer_id.values(), key=lambda contact: contact.title.lower())

    async def list_group_chats(self, client: TelegramClient, include_avatars: bool = True) -> List[TelegramDialog]:
        groups = []
        async for dialog in client.iter_dialogs():
            if not bool(getattr(dialog, "is_group", False)) and not bool(getattr(dialog, "is_channel", False)):
                continue
            entity = dialog.entity
            avatar, avatar_photo_id, avatar_download_failed = await self.small_avatar(
                client,
                entity,
                include_content=include_avatars,
            )
            groups.append(
                TelegramDialog(
                    peer_id=int(dialog.id),
                    title=dialog.name or str(dialog.id),
                    username=getattr(entity, "username", None),
                    phone=getattr(entity, "phone", None),
                    is_group=bool(getattr(dialog, "is_group", False)),
                    is_channel=bool(getattr(dialog, "is_channel", False)),
                    avatar=avatar,
                    avatar_photo_id=avatar_photo_id,
                    avatar_download_failed=avatar_download_failed,
                )
            )
        return sorted(groups, key=lambda group: group.title.lower())

    async def send_direct_message(
        self,
        client: TelegramClient,
        peer_id: int,
        body: str,
        reply_to_message_id: Optional[str] = None,
        forward_reference: Optional[TelegramForwardReference] = None,
        media: tuple = (),
    ) -> Optional[str]:
        entity = await self._resolve_direct_entity(client, peer_id)
        if media:
            return await self._send_media_message(
                client,
                entity,
                body,
                media,
                reply_to_message_id=reply_to_message_id,
            )
        if forward_reference is not None:
            sent = await self._forward_message(client, entity, forward_reference)
            if body:
                await client.send_message(entity, body)
            return sent
        log.debug(
            "Resolved Telegram direct message entity peer_id=%s entity_type=%s body_length=%s reply_to=%s",
            peer_id,
            type(entity).__name__,
            len(body),
            reply_to_message_id,
        )
        sent = await client.send_message(
            entity,
            body,
            reply_to=int(reply_to_message_id) if reply_to_message_id else None,
        )
        message_id = getattr(sent, "id", None)
        return str(message_id) if message_id is not None else None

    async def send_group_message(
        self,
        client: TelegramClient,
        peer_id: int,
        body: str,
        reply_to_message_id: Optional[str] = None,
        forward_reference: Optional[TelegramForwardReference] = None,
        media: tuple = (),
    ) -> Optional[str]:
        entity = await self._resolve_group_entity(client, peer_id)
        if media:
            return await self._send_media_message(
                client,
                entity,
                body,
                media,
                reply_to_message_id=reply_to_message_id,
            )
        if forward_reference is not None:
            sent = await self._forward_message(client, entity, forward_reference)
            if body:
                await client.send_message(entity, body)
            return sent
        log.debug(
            "Resolved Telegram group message entity peer_id=%s entity_type=%s body_length=%s reply_to=%s",
            peer_id,
            type(entity).__name__,
            len(body),
            reply_to_message_id,
        )
        sent = await client.send_message(
            entity,
            body,
            reply_to=int(reply_to_message_id) if reply_to_message_id else None,
        )
        message_id = getattr(sent, "id", None)
        return str(message_id) if message_id is not None else None

    async def _resolve_direct_entity(self, client: TelegramClient, peer_id: int):
        result = await client(GetContactsRequest(hash=0))
        for user in getattr(result, "users", []):
            if int(user.id) == peer_id:
                log.debug("Resolved Telegram peer_id=%s from address-book contacts", peer_id)
                return user

        async for dialog in client.iter_dialogs():
            if bool(getattr(dialog, "is_group", False)) or bool(getattr(dialog, "is_channel", False)):
                continue
            if int(dialog.id) == peer_id:
                log.debug("Resolved Telegram peer_id=%s from private dialogs", peer_id)
                return dialog.entity

        log.debug("Could not resolve Telegram direct peer_id=%s from contacts or private dialogs", peer_id)
        raise ValueError("Telegram direct chat is not available. Send /sync-contacts and try again.")

    async def _resolve_group_entity(self, client: TelegramClient, peer_id: int):
        async for dialog in client.iter_dialogs():
            if not bool(getattr(dialog, "is_group", False)) and not bool(getattr(dialog, "is_channel", False)):
                continue
            if int(dialog.id) == peer_id:
                log.debug("Resolved Telegram group peer_id=%s from dialogs", peer_id)
                return dialog.entity

        log.debug("Could not resolve Telegram group peer_id=%s from dialogs", peer_id)
        raise ValueError("Telegram group chat is not available.")

    async def _forward_message(
        self,
        client: TelegramClient,
        target_entity,
        forward_reference: TelegramForwardReference,
    ) -> Optional[str]:
        source_entity = await self._resolve_any_entity(client, forward_reference.source_peer_id)
        log.debug(
            "Forwarding Telegram message source_peer_id=%s message_id=%s target_entity_type=%s",
            forward_reference.source_peer_id,
            forward_reference.message_id,
            type(target_entity).__name__,
        )
        sent = await client.forward_messages(
            target_entity,
            int(forward_reference.message_id),
            from_peer=source_entity,
        )
        if isinstance(sent, list):
            sent = sent[0] if sent else None
        message_id = getattr(sent, "id", None)
        return str(message_id) if message_id is not None else None

    async def _send_media_message(
        self,
        client: TelegramClient,
        target_entity,
        body: str,
        media: tuple,
        reply_to_message_id: Optional[str] = None,
    ) -> Optional[str]:
        media_items = [
            item for item in media if str(getattr(item, "url", "") or "").startswith(("http://", "https://"))
        ]
        files = [item.url for item in media_items]
        if not media_items:
            if body:
                sent = await client.send_message(
                    target_entity,
                    body,
                    reply_to=int(reply_to_message_id) if reply_to_message_id else None,
                )
                message_id = getattr(sent, "id", None)
                return str(message_id) if message_id is not None else None
            return None
        try:
            sent = await self._send_file(
                client,
                target_entity,
                files if len(files) > 1 else files[0],
                body,
                reply_to_message_id=reply_to_message_id,
            )
        except (WebpageCurlFailedError, WebpageMediaEmptyError):
            sent = await self._send_downloaded_media_or_link_fallback(
                client,
                target_entity,
                media_items,
                body,
                reply_to_message_id=reply_to_message_id,
            )
        if isinstance(sent, list):
            sent = sent[-1] if sent else None
        message_id = getattr(sent, "id", None)
        return str(message_id) if message_id is not None else None

    async def _send_file(
        self,
        client: TelegramClient,
        target_entity,
        files,
        body: str,
        reply_to_message_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_size: Optional[int] = None,
    ):
        return await client.send_file(
            target_entity,
            files,
            caption=body or None,
            reply_to=int(reply_to_message_id) if reply_to_message_id else None,
            mime_type=mime_type,
            file_size=file_size,
        )

    async def _send_downloaded_media_or_link_fallback(
        self,
        client: TelegramClient,
        target_entity,
        media_items: list,
        body: str,
        reply_to_message_id: Optional[str] = None,
    ):
        downloaded = []
        try:
            downloaded = await self._download_outgoing_media_files(media_items)
            paths = [item["path"] for item in downloaded]
            first = downloaded[0] if len(downloaded) == 1 else {}
            return await self._send_file(
                client,
                target_entity,
                paths if len(paths) > 1 else paths[0],
                body,
                reply_to_message_id=reply_to_message_id,
                mime_type=first.get("mime_type"),
                file_size=first.get("file_size"),
            )
        except Exception:
            log.warning("Could not upload XMPP media URL to Telegram; sending link fallback", exc_info=True)
            return await client.send_message(
                target_entity,
                self._media_link_fallback_body(body, media_items),
                reply_to=int(reply_to_message_id) if reply_to_message_id else None,
            )
        finally:
            for item in downloaded:
                try:
                    os.unlink(item["path"])
                except OSError:
                    log.debug("Could not remove temporary Telegram upload file %s", item["path"], exc_info=True)

    async def _download_outgoing_media_files(self, media_items: list) -> list:
        downloaded = []
        async with aiohttp.ClientSession() as session:
            for item in media_items:
                downloaded.append(await self._download_outgoing_media_file(session, item))
        return downloaded

    async def _download_outgoing_media_file(self, session: aiohttp.ClientSession, media) -> dict:
        url = str(getattr(media, "url", "") or "")
        async with session.get(url) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > MAX_OUTGOING_MEDIA_BYTES:
                raise ValueError("Outgoing XMPP media is too large for Telegram upload fallback")
            path = self._temporary_media_path(media)
            size = 0
            try:
                with open(path, "wb") as handle:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_OUTGOING_MEDIA_BYTES:
                            raise ValueError("Outgoing XMPP media is too large for Telegram upload fallback")
                        handle.write(chunk)
            except Exception:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                raise
        return {
            "path": path,
            "file_size": size,
            "mime_type": str(getattr(media, "mime_type", "") or "") or None,
        }

    @classmethod
    def _temporary_media_path(cls, media) -> str:
        name = str(getattr(media, "name", "") or "").strip()
        if not name:
            path = unquote(urlsplit(str(getattr(media, "url", "") or "")).path)
            name = posixpath.basename(path)
        suffix = os.path.splitext(name)[1] if name else ""
        handle = tempfile.NamedTemporaryFile(prefix="xmpp-telegram-upload-", suffix=suffix, delete=False)
        path = handle.name
        handle.close()
        return path

    @staticmethod
    def _media_link_fallback_body(body: str, media_items: list) -> str:
        urls = [str(getattr(item, "url", "") or "") for item in media_items]
        parts = [part for part in (body.strip(), "\n".join(urls)) if part]
        return "\n".join(parts)

    async def _resolve_any_entity(self, client: TelegramClient, peer_id: int):
        if peer_id >= 0:
            return await self._resolve_direct_entity(client, peer_id)
        return await self._resolve_group_entity(client, peer_id)

    async def get_message_media(self, client: TelegramClient, peer_id: int, message_id: str):
        entity = await self._resolve_any_entity(client, peer_id)
        message = await client.get_messages(entity, ids=int(message_id))
        if message is None or getattr(message, "media", None) is None:
            raise FileNotFoundError("Telegram message media is not available")
        return message.media

    def iter_media_download(
        self,
        client: TelegramClient,
        media,
        *,
        request_size: int,
        file_size: Optional[int] = None,
    ):
        return client.iter_download(
            media,
            request_size=request_size,
            file_size=file_size,
        )

    @staticmethod
    def _user_title(user) -> str:
        first_name = getattr(user, "first_name", None)
        last_name = getattr(user, "last_name", None)
        full_name = " ".join(part for part in (first_name, last_name) if part)
        username = getattr(user, "username", None)
        phone = getattr(user, "phone", None)
        if full_name:
            return full_name
        if username:
            return "@%s" % username
        if phone:
            return "+%s" % phone
        return str(getattr(user, "id", "unknown"))

    async def small_avatar(self, client: TelegramClient, entity, include_content: bool = True):
        photo = getattr(entity, "photo", None)
        photo_id = getattr(photo, "photo_id", None)
        if photo_id is None:
            return None, None, False
        photo_id = str(photo_id)
        if not include_content:
            return None, photo_id, False
        try:
            content = await client.download_profile_photo(entity, file=bytes, download_big=False)
        except Exception:
            log.debug(
                "Could not download Telegram small avatar peer_id=%s photo_id=%s",
                getattr(entity, "id", "unknown"),
                photo_id,
                exc_info=True,
            )
            return None, photo_id, True
        if not content:
            return None, photo_id, True
        return TelegramAvatar(photo_id=photo_id, content=content), photo_id, False
