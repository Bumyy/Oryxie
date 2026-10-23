import os
import logging
from typing import Optional
import discord
from discord.ext import commands
from aiohttp import web

from cogs.dm_logging import post_dm_report

logger = logging.getLogger("oryxie.notifications")

EVENT_COLORS = {
    "task_assigned": 0x6366F1,   # Indigo
    "task_updated": 0x3B82F6,    # Blue
    "task_moved": 0xF59E0B,      # Amber
    "task_completed": 0x10B981,  # Emerald
    "task_deleted": 0xEF4444,    # Red
    "task_comment": 0x8B5CF6,    # Violet
}

EVENT_TITLES = {
    "task_assigned": "Task Assigned",
    "task_updated": "Task Updated",
    "task_moved": "Task Moved",
    "task_completed": "Task Completed",
    "task_deleted": "Task Deleted",
    "task_comment": "New Task Comment",
}

class NotificationCog(commands.Cog):
    """
    HTTP notification server for Oryxpace integration.
    Receives task notifications via HTTP POST and sends DMs to staff members.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        
        self.host = os.getenv("NOTIFICATION_SERVER_HOST", "0.0.0.0")
        self.port = int(os.getenv("NOTIFICATION_SERVER_PORT", "8081"))
        self.api_key = os.getenv("NOTIFICATION_API_KEY", "").strip()

    async def cog_load(self):
        """Start the aiohttp web server when the cog is loaded."""
        self.app = web.Application()
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_post("/notify", self.handle_notify)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        try:
            self.site = web.TCPSite(self.runner, self.host, self.port)
            await self.site.start()
            logger.info(f"Oryxpace notification server running on http://{self.host}:{self.port}")
            print(f"Oryxpace notification server running on http://{self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start notification server on {self.host}:{self.port}: {e}")
            print(f"[NOTIFICATION SERVER ERROR] Failed to start on {self.host}:{self.port}: {e}")

    async def cog_unload(self):
        """Clean up the web server when the cog is unloaded."""
        if self.runner:
            await self.runner.cleanup()
            logger.info("Oryxpace notification server stopped.")
            print("Oryxpace notification server stopped.")

    def _is_authorized(self, request: web.Request) -> bool:
        """Verify API key if NOTIFICATION_API_KEY is configured."""
        if not self.api_key:
            return True

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token == self.api_key:
                return True

        api_key_header = request.headers.get("X-API-Key", "").strip()
        if api_key_header == self.api_key:
            return True

        return False

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "ok",
            "bot_ready": self.bot.is_ready() and not self.bot.is_closed(),
            "bot_user": str(self.bot.user) if self.bot.user else None,
            "latency_ms": round(self.bot.latency * 1000) if self.bot.is_ready() else None
        })

    async def handle_notify(self, request: web.Request) -> web.Response:
        """
        Send a task notification DM to a Discord user.
        
        Expected JSON Body:
        {
            "discord_user_id": "123456789012345678",   (Required)
            "task_title": "Fix login bug",              (Optional)
            "task_url": "https://oryxpace.../tasks/42", (Optional)
            "project_name": "Core Dev",                (Optional)
            "event_type": "task_assigned",             (Optional)
            "message": "You were assigned to this",    (Optional)
            "pilot_name": "Captain John",              (Optional)
            "task_id": 42                              (Optional)
        }
        """
        # 1. Check authorization
        if not self._is_authorized(request):
            return web.json_response(
                {"status": "error", "error": "Unauthorized. Invalid or missing API key.", "code": "unauthorized"},
                status=401
            )

        # 2. Check if bot is connected
        if self.bot.is_closed():
            return web.json_response(
                {"status": "error", "error": "Bot is currently disconnected from Discord.", "code": "bot_disconnected"},
                status=503
            )

        # 3. Parse JSON payload
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"status": "error", "error": "Malformed JSON payload provided.", "code": "invalid_json"},
                status=400
            )

        if not isinstance(data, dict):
            return web.json_response(
                {"status": "error", "error": "Request body must be a JSON object.", "code": "invalid_payload"},
                status=400
            )

        # 4. Validate discord_user_id
        raw_user_id = data.get("discord_user_id")
        if not raw_user_id:
            return web.json_response(
                {"status": "error", "error": "'discord_user_id' is required.", "code": "missing_user_id"},
                status=400
            )

        try:
            user_id = int(raw_user_id)
        except (ValueError, TypeError):
            return web.json_response(
                {"status": "error", "error": f"Invalid 'discord_user_id': {raw_user_id}. Must be an integer or snowflake string.", "code": "invalid_user_id"},
                status=400
            )

        # 5. Build notification details and fetch user
        embed = self._build_embed(data)
        event_type = str(data.get("event_type", "task_updated")).strip().lower()
        event_label = EVENT_TITLES.get(
            event_type,
            "Oryxpace Task Notification",
        )
        task_title = str(data.get("task_title") or "Untitled Task")[:200]

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                await self._report_dm_delivery(
                    user_id,
                    None,
                    event_label,
                    task_title,
                    "Not delivered",
                    "Discord user was not found.",
                )
                return web.json_response(
                    {"status": "error", "error": f"Discord user {user_id} was not found.", "code": "user_not_found"},
                    status=404
                )
            except discord.HTTPException as e:
                await self._report_dm_delivery(
                    user_id,
                    None,
                    event_label,
                    task_title,
                    "Not delivered",
                    "Discord returned an HTTP error while fetching the user.",
                )
                logger.error(f"Discord API error fetching user {user_id}: {e}")
                return web.json_response(
                    {"status": "error", "error": f"Discord API error fetching user: {e}", "code": "discord_api_error"},
                    status=502
                )

        # 7. Deliver DM
        try:
            await user.send(embed=embed)
            await self._report_dm_delivery(
                user_id,
                user,
                event_label,
                task_title,
                "Delivered",
            )
            logger.info(f"Oryxpace notification delivered to {user} ({user_id})")
            return web.json_response({
                "status": "delivered",
                "discord_user_id": str(user_id),
                "username": str(user)
            }, status=200)

        except discord.Forbidden:
            await self._report_dm_delivery(
                user_id,
                user,
                event_label,
                task_title,
                "Not delivered",
                "Discord rejected the DM (user privacy settings or no mutual server).",
            )
            logger.warning(f"Forbidden: Cannot DM user {user_id}. DMs disabled or no shared guild.")
            return web.json_response({
                "status": "error",
                "error": f"Cannot send DM to user {user_id}. The user has direct messages disabled or does not share a server with the bot.",
                "code": "dms_disabled"
            }, status=403)

        except discord.HTTPException as e:
            await self._report_dm_delivery(
                user_id,
                user,
                event_label,
                task_title,
                "Not delivered",
                "Discord returned an HTTP error while sending the DM.",
            )
            logger.error(f"Failed to send DM to {user_id}: {e}")
            return web.json_response({
                "status": "error",
                "error": f"Discord HTTP error sending DM: {e}",
                "code": "discord_send_failed"
            }, status=502)

        except Exception as e:
            await self._report_dm_delivery(
                user_id,
                user,
                event_label,
                task_title,
                "Not delivered",
                "An unexpected error occurred while sending the DM.",
            )
            logger.exception(f"Unexpected error sending DM to {user_id}: {e}")
            return web.json_response({
                "status": "error",
                "error": f"Internal server error: {e}",
                "code": "internal_error"
            }, status=500            )

    async def _report_dm_delivery(
        self,
        user_id: int,
        user,
        event_label: str,
        task_title: str,
        delivery_status: str,
        reason: str = "",
    ) -> None:
        report = (
            f"📨 **Bot DM report · Oryxpace task notification**\n"
            f"**Recipient:** <@{user_id}>"
            f"{f' ({user})' if user is not None else ''}\n"
            f"**Notification:** {event_label} · {task_title}\n"
            f"**Status:** {delivery_status}"
        )
        if reason:
            report += f"\n**Details:** {reason}"
        await post_dm_report(
            self.bot,
            report,
            logger,
            f"Oryxpace notification for user {user_id}",
        )

    def _build_embed(self, data: dict) -> discord.Embed:
        """Construct the Discord embed for the task notification."""
        task_title = data.get("task_title", "Untitled Task")
        task_url = data.get("task_url", "").strip()
        project_name = data.get("project_name", "").strip()
        message = data.get("message", "").strip()
        event_type = data.get("event_type", "task_updated").strip().lower()
        pilot_name = data.get("pilot_name", "").strip()

        color = EVENT_COLORS.get(event_type, 0x6366F1)
        title = EVENT_TITLES.get(event_type, "Oryxpace Task Notification")

        embed = discord.Embed(
            title=f"📋 {title}",
            description=message if message else None,
            color=color,
            timestamp=discord.utils.utcnow()
        )

        # Task link or title
        task_display = f"[{task_title}]({task_url})" if task_url else task_title
        embed.add_field(name="Task", value=task_display, inline=False)

        if project_name:
            embed.add_field(name="Project", value=project_name, inline=True)

        if pilot_name:
            embed.add_field(name="Assigned To", value=pilot_name, inline=True)

        embed.set_footer(text="Oryxpace Staff System")
        return embed

async def setup(bot: commands.Bot):
    await bot.add_cog(NotificationCog(bot))
