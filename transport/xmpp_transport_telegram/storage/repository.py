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
                    xmpp_account_id BIGINT NOT NULL REFERENCES xmpp_accounts(id) ON DELETE CASCADE,
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
