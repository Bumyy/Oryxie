import os
import aiomysql
from dotenv import load_dotenv

load_dotenv()

class DatabaseManager:
    def __init__(self, bot):
        self.bot = bot
        self.pool = None
        self.db_config = {
            "host": os.getenv("DB_HOST"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
            "db": os.getenv("DB_NAME"),
            "autocommit": True,
            "charset": "utf8mb4",
            "cursorclass": aiomysql.DictCursor
        }

    async def connect(self):
        """Establishes a connection pool to the MySQL database."""
        if self.pool is None:
            try:
                self.pool = await aiomysql.create_pool(
                    loop=self.bot.loop,
                    **self.db_config,
                    minsize=1,
                    maxsize=10
                )
                print("MySQL connection pool created successfully!")
            except Exception as e:
                print(f"Failed to connect to MySQL: {e}")
                self.pool = None

    async def close(self):
        """Closes the MySQL connection pool."""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            print("MySQL connection pool closed.")
            self.pool = None

    async def fetch_one(self, query: str, args: tuple = None):
        """Executes a SELECT query and returns the first row."""
        if not self.pool:
            print("Error: Database pool not initialized.")
            return None
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute(query, args)
                    result = await cursor.fetchone()
                    return result
                except Exception as e:
                    print(f"Error fetching one: {e} - Query: {query} Args: {args}")
                    return None

    async def fetch_all(self, query: str, args: tuple = None):
        """Executes a SELECT query and returns all rows."""
        if not self.pool:
            print("Error: Database pool not initialized.")
            return []
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute(query, args)
                    result = await cursor.fetchall()
                    return result
                except Exception as e:
                    print(f"Error fetching all: {e} - Query: {query} Args: {args}")
                    return []

    async def execute(self, query: str, args: tuple = None):
        """Executes an INSERT, UPDATE, or DELETE query."""
        if not self.pool:
            print("Error: Database pool not initialized.")
            return 0
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute(query, args)
                    return cursor.rowcount
                except Exception as e:
                    print(f"Error executing query: {e} - Query: {query} Args: {args}")
                    return 0