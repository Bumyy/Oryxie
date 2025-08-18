import os
import aiomysql
import asyncio
from dotenv import load_dotenv

load_dotenv()

class DatabaseManager:
    def __init__(self, bot):
        self.bot = bot
        self._pool = None 
        self._db_config = {
            "host": os.getenv("DB_HOST"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
            "db": os.getenv("DB_NAME"),
            "autocommit": True,
            "charset": "utf8mb4",
            "cursorclass": aiomysql.DictCursor
        }
        self._lock = asyncio.Lock()

    async def connect(self):
        """Establishes a connection pool to the MySQL database."""
        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await aiomysql.create_pool(
                        **self._db_config,
                        minsize=1,
                        maxsize=10,
                        connect_timeout=10 
                    )
                    print("MySQL connection pool created successfully!")
                except Exception as e:
                    print(f"CRITICAL: Failed to connect to MySQL: {e}")
                    self._pool = None

    async def close(self):
        """Closes the MySQL connection pool if it exists."""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            print("MySQL connection pool closed.")
            self._pool = None
            
    async def _get_pool(self):
        """Gets the pool, connecting if it doesn't exist."""
        if self._pool is None:
            await self.connect()
        return self._pool

    async def _execute_query(self, query: str, args: tuple = None, fetch_type: str = 'none'):
        """
        The central workhorse method for all database operations.
        Includes reconnection and retry logic.
        fetch_type can be 'one', 'all', or 'none'.
        """
        for attempt in range(2):
            try:
                pool = await self._get_pool()
                if not pool:
                    print("Error: Database pool is not available.")
                    return None if fetch_type == 'one' else [] if fetch_type == 'all' else 0

                async with pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute(query, args)
                        
                        if fetch_type == 'one':
                            return await cursor.fetchone()
                        elif fetch_type == 'all':
                            return await cursor.fetchall()
                        else:
                            return cursor.rowcount

            except aiomysql.OperationalError as e:
                print(f"OperationalError on attempt {attempt + 1}: {e}. Query: {query}")
                if attempt == 0:
                    print("Closing stale pool and attempting to reconnect...")
                    await self.close()
                    await asyncio.sleep(1) 
                    continue 
                else:
                    print("Failed to execute query after reconnecting. The database might be down.")
            except Exception as e:
                print(f"An unexpected database error occurred: {e} - Query: {query} Args: {args}")
                break
        
        return None if fetch_type == 'one' else [] if fetch_type == 'all' else 0

    async def fetch_one(self, query: str, args: tuple = None):
        """Executes a SELECT query and returns the first row."""
        return await self._execute_query(query, args, fetch_type='one')

    async def fetch_all(self, query: str, args: tuple = None):
        """Executes a SELECT query and returns all rows."""
        return await self._execute_query(query, args, fetch_type='all')

    async def execute(self, query: str, args: tuple = None):
        """Executes an INSERT, UPDATE, or DELETE query and returns row count."""
        return await self._execute_query(query, args, fetch_type='none')