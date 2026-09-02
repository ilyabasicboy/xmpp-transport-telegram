import asyncpg


class Repository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.database_url)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def migrate(self) -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS xmpp_accounts (
                    id BIGSERIAL PRIMARY KEY,
                    xmpp_jid TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS telegram_sessions (
                    id BIGSERIAL PRIMARY KEY,
                    xmpp_account_id BIGINT NOT NULL UNIQUE REFERENCES xmpp_accounts(id) ON DELETE CASCADE,
                    telegram_user_id BIGINT UNIQUE,
                    phone TEXT,
                    encrypted_session TEXT NOT NULL,
                    connected BOOLEAN NOT NULL DEFAULT false,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS auth_attempts (
                    id BIGSERIAL PRIMARY KEY,
                    xmpp_account_id BIGINT NOT NULL REFERENCES xmpp_accounts(id) ON DELETE CASCADE,
                    phone TEXT NOT NULL,
                    phone_code_hash TEXT,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE UNIQUE INDEX IF NOT EXISTS telegram_sessions_xmpp_account_id_idx
                ON telegram_sessions (xmpp_account_id);

                CREATE TABLE IF NOT EXISTS synced_roster_items (
                    id BIGSERIAL PRIMARY KEY,
                    xmpp_jid TEXT NOT NULL,
                    item_jid TEXT NOT NULL,
                    item_kind TEXT NOT NULL,
                    sync_signature TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (xmpp_jid, item_jid)
                );

                CREATE INDEX IF NOT EXISTS idx_synced_roster_items_xmpp_jid
                ON synced_roster_items (xmpp_jid);

                CREATE TABLE IF NOT EXISTS telegram_avatar_files (
                    content_hash TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    bytes_count INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    accessed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS telegram_contact_avatars (
                    id BIGSERIAL PRIMARY KEY,
                    owner_jid TEXT NOT NULL,
                    contact_jid TEXT NOT NULL,
                    peer_id BIGINT NOT NULL,
                    photo_id TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    content_hash TEXT NOT NULL REFERENCES telegram_avatar_files(content_hash),
                    avatar_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    bytes_count INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (owner_jid, contact_jid, variant)
                );

                CREATE INDEX IF NOT EXISTS idx_telegram_contact_avatars_hash
                ON telegram_contact_avatars (content_hash);

                CREATE TABLE IF NOT EXISTS telegram_media_references (
                    token TEXT PRIMARY KEY,
                    owner_jid TEXT NOT NULL,
                    peer_id BIGINT NOT NULL,
                    message_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    bytes_count BIGINT,
                    width INTEGER,
                    height INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    accessed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE INDEX IF NOT EXISTS idx_telegram_media_refs_owner
                ON telegram_media_references (owner_jid);
                """
            )

    async def ensure_xmpp_account(self, xmpp_jid: str) -> int:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            return await connection.fetchval(
                """
                INSERT INTO xmpp_accounts (xmpp_jid)
                VALUES ($1)
                ON CONFLICT (xmpp_jid) DO UPDATE SET updated_at = now()
                RETURNING id
                """,
                xmpp_jid,
            )

    async def get_telegram_session(self, xmpp_account_id: int):
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            return await connection.fetchrow(
                """
                SELECT telegram_user_id, phone, encrypted_session, connected
                FROM telegram_sessions
                WHERE xmpp_account_id = $1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                xmpp_account_id,
            )

    async def upsert_telegram_session(
        self,
        xmpp_account_id: int,
        telegram_user_id: int,
        phone,
        encrypted_session: str,
        connected: bool,
    ):
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    telegram_user_id,
                )
                current_owner = await connection.fetchval(
                    """
                    SELECT xmpp_jid
                    FROM xmpp_accounts
                    WHERE id = $1
                    """,
                    xmpp_account_id,
                )
                previous_owner = await connection.fetchval(
                    """
                    SELECT accounts.xmpp_jid
                    FROM telegram_sessions AS sessions
                    JOIN xmpp_accounts AS accounts
                        ON accounts.id = sessions.xmpp_account_id
                    WHERE sessions.telegram_user_id = $1
                        AND sessions.xmpp_account_id != $2
                    """,
                    telegram_user_id,
                    xmpp_account_id,
                )
                if previous_owner is not None:
                    await connection.execute(
                        """
                        DELETE FROM telegram_sessions
                        WHERE telegram_user_id = $1
                        """,
                        telegram_user_id,
                    )
                    await connection.execute(
                        """
                        DELETE FROM synced_roster_items
                        WHERE xmpp_jid = $1
                        """,
                        previous_owner,
                    )
                    await connection.execute(
                        """
                        DELETE FROM telegram_contact_avatars
                        WHERE owner_jid = $1
                        """,
                        previous_owner,
                    )

                # A personal Telegram account can belong to only one XMPP user
                # at a time.  The transaction first removes any previous owner,
                # then upserts the current owner so the unique Telegram id never
                # leaks as an async task exception.
                await connection.execute(
                    """
                    INSERT INTO telegram_sessions (
                        xmpp_account_id,
                        telegram_user_id,
                        phone,
                        encrypted_session,
                        connected
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (xmpp_account_id) DO UPDATE SET
                        telegram_user_id = EXCLUDED.telegram_user_id,
                        phone = EXCLUDED.phone,
                        encrypted_session = EXCLUDED.encrypted_session,
                        connected = EXCLUDED.connected,
                        updated_at = now()
                    """,
                    xmpp_account_id,
                    telegram_user_id,
                    phone,
                    encrypted_session,
                    connected,
                )
                if previous_owner == current_owner:
                    return None
                return previous_owner

    async def delete_telegram_session(self, xmpp_account_id: int) -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                owner_jid = await connection.fetchval(
                    """
                    SELECT xmpp_jid
                    FROM xmpp_accounts
                    WHERE id = $1
                    """,
                    xmpp_account_id,
                )
                await connection.execute(
                    "DELETE FROM telegram_sessions WHERE xmpp_account_id = $1",
                    xmpp_account_id,
                )
                if owner_jid is not None:
                    await connection.execute(
                        """
                        DELETE FROM telegram_contact_avatars
                        WHERE owner_jid = $1
                        """,
                        owner_jid,
                    )

    async def list_connected_telegram_sessions(self):
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            return await connection.fetch(
                """
                SELECT
                    accounts.xmpp_jid,
                    sessions.encrypted_session
                FROM telegram_sessions AS sessions
                JOIN xmpp_accounts AS accounts
                    ON accounts.id = sessions.xmpp_account_id
                WHERE sessions.connected = true
                ORDER BY accounts.xmpp_jid
                """
            )

    async def get_synced_roster_item_signature(self, xmpp_jid: str, item_jid: str):
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            return await connection.fetchval(
                """
                SELECT sync_signature
                FROM synced_roster_items
                WHERE xmpp_jid = $1 AND item_jid = $2
                """,
                xmpp_jid,
                item_jid,
            )

    async def set_synced_roster_item_signature(
        self,
        xmpp_jid: str,
        item_jid: str,
        item_kind: str,
        sync_signature: str,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO synced_roster_items (
                    xmpp_jid, item_jid, item_kind, sync_signature, updated_at
                ) VALUES ($1, $2, $3, $4, now())
                ON CONFLICT (xmpp_jid, item_jid) DO UPDATE SET
                    item_kind = EXCLUDED.item_kind,
                    sync_signature = EXCLUDED.sync_signature,
                    updated_at = now()
                """,
                xmpp_jid,
                item_jid,
                item_kind,
                sync_signature,
            )

    async def delete_synced_roster_item(self, xmpp_jid: str, item_jid: str) -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM synced_roster_items
                WHERE xmpp_jid = $1 AND item_jid = $2
                """,
                xmpp_jid,
                item_jid,
            )

    async def upsert_avatar_file(
        self,
        content_hash: str,
        relative_path: str,
        mime_type: str,
        bytes_count: int,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO telegram_avatar_files (
                    content_hash, relative_path, mime_type, bytes_count
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (content_hash) DO UPDATE SET
                    accessed_at = now()
                """,
                content_hash,
                relative_path,
                mime_type,
                bytes_count,
            )

    async def get_avatar_file(self, content_hash: str):
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            return await connection.fetchrow(
                """
                UPDATE telegram_avatar_files
                SET accessed_at = now()
                WHERE content_hash = $1
                RETURNING relative_path, mime_type, bytes_count
                """,
                content_hash,
            )

    async def delete_avatar_file(self, content_hash: str) -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM telegram_avatar_files
                WHERE content_hash = $1
                AND NOT EXISTS (
                    SELECT 1
                    FROM telegram_contact_avatars
                    WHERE telegram_contact_avatars.content_hash = telegram_avatar_files.content_hash
                )
                """,
                content_hash,
            )

    async def list_unreferenced_avatar_files(self, ttl_days: int):
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            return await connection.fetch(
                """
                SELECT files.content_hash, files.relative_path
                FROM telegram_avatar_files AS files
                LEFT JOIN telegram_contact_avatars AS avatars
                    ON avatars.content_hash = files.content_hash
                WHERE avatars.id IS NULL
                    AND files.accessed_at < now() - make_interval(days => $1)
                ORDER BY files.accessed_at
                """,
                ttl_days,
            )

    async def upsert_contact_avatar(
        self,
        owner_jid: str,
        contact_jid: str,
        peer_id: int,
        photo_id: str,
        variant: str,
        content_hash: str,
        avatar_id: str,
        url: str,
        mime_type: str,
        bytes_count: int,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO telegram_contact_avatars (
                    owner_jid,
                    contact_jid,
                    peer_id,
                    photo_id,
                    variant,
                    content_hash,
                    avatar_id,
                    url,
                    mime_type,
                    bytes_count,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, now())
                ON CONFLICT (owner_jid, contact_jid, variant) DO UPDATE SET
                    peer_id = EXCLUDED.peer_id,
                    photo_id = EXCLUDED.photo_id,
                    content_hash = EXCLUDED.content_hash,
                    avatar_id = EXCLUDED.avatar_id,
                    url = EXCLUDED.url,
                    mime_type = EXCLUDED.mime_type,
                    bytes_count = EXCLUDED.bytes_count,
                    updated_at = now()
                """,
                owner_jid,
                contact_jid,
                peer_id,
                photo_id,
                variant,
                content_hash,
                avatar_id,
                url,
                mime_type,
                bytes_count,
            )

    async def delete_contact_avatar(self, owner_jid: str, contact_jid: str, variant: str = "small") -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM telegram_contact_avatars
                WHERE owner_jid = $1
                    AND contact_jid = $2
                    AND variant = $3
                """,
                owner_jid,
                contact_jid,
                variant,
            )

    async def create_media_reference(
        self,
        token: str,
        owner_jid: str,
        peer_id: int,
        message_id: str,
        file_name: str,
        mime_type: str,
        bytes_count,
        width,
        height,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO telegram_media_references (
                    token,
                    owner_jid,
                    peer_id,
                    message_id,
                    file_name,
                    mime_type,
                    bytes_count,
                    width,
                    height
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (token) DO NOTHING
                """,
                token,
                owner_jid,
                peer_id,
                message_id,
                file_name,
                mime_type,
                bytes_count,
                width,
                height,
            )

    async def get_media_reference(self, token: str):
        if self.pool is None:
            raise RuntimeError("Repository is not connected")
        async with self.pool.acquire() as connection:
            return await connection.fetchrow(
                """
                UPDATE telegram_media_references
                SET accessed_at = now()
                WHERE token = $1
                RETURNING
                    owner_jid,
                    peer_id,
                    message_id,
                    file_name,
                    mime_type,
                    bytes_count,
                    width,
                    height
                """,
                token,
            )
