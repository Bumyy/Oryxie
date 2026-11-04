# Discord Cogs: Architecture & Boundaries

Discord Cogs in Oryxie represent the **Presentation Layer**. They interface with the Discord Gateway, process slash commands, render user interfaces (embeds, views, modals), and manage background scheduling loops.

---

## 🎯 Cog Responsibilities (What Cogs MUST Do)

1. **User Interaction & Input Validation**:
   - Register slash commands (`@app_commands.command`), user/message context menus, and traditional prefix commands.
   - Validate Discord-specific input constraints (e.g. user roles, channel permissions, input sanitization).
   - Defer long-running interactions immediately (`await interaction.response.defer()`) if processing may exceed 2.5 seconds.
2. **Presentation & Rendering**:
   - Construct rich Discord embeds (`discord.Embed`), UI components (`discord.ui.View`, `discord.ui.Button`, `discord.ui.Select`, `discord.ui.Modal`).
   - Format user-friendly status, confirmation, and error messages.
3. **Delegation to Services & Models**:
   - Act as orchestrators: parse user input, invoke the appropriate method on a **Service** or **Database Model**, and present the returned data.
4. **Lifecycle & Scheduled Tasks**:
   - Manage domain-specific background loops using `discord.ext.tasks.loop`.
   - Implement `cog_load()` and `cog_unload()` to initialize and gracefully terminate background resources or tasks.
5. **Discord Event Handling**:
   - Listen to Discord events (`@commands.Cog.listener`) such as `on_member_join`, `on_member_update`, or `on_message` when needed for community workflows.

---

## 🚫 Cog Anti-Patterns (What Cogs MUST NOT Do)

| Anti-Pattern | Why It Is Prohibited | Proper Alternative |
| :--- | :--- | :--- |
| **Direct Raw SQL Queries** | Violates separation of concerns, exposes database schema in UI code, prevents query reuse and testing. | Call methods on `self.bot.pilots_model`, `self.bot.routes_model`, etc. |
| **Raw External HTTP Requests** | Bypasses rate limiting, session pooling, and error handling. | Call `self.bot.if_api_manager`, `self.bot.cc_api_manager`, or a dedicated service. |
| **Heavy Business Logic / Math** | Makes business logic untestable without mocking the Discord API. | Move logic to a class in `services/` and call it from the Cog. |
| **CPU-Bound Blocking Operations** | ReportLab PDF rendering, image processing, or heavy loops will block the Discord Gateway heartbeat and crash the bot. | Wrap in `asyncio.to_thread()` or delegate to a background task. |
| **Long-Lived In-Memory State** | In-memory variables in Cogs are lost during reloads or crashes. | Store persistent state in MySQL via `database/` models. |
| **Silent Exception Swallowing** | Hiding exceptions makes debugging silent failures impossible. | Log with `logging.exception()` and reply with a clean user-facing error message. |

---

## ⏱️ The Discord Interaction Rule (3 Seconds)

Discord will automatically fail any slash command interaction with **"The application did not respond"** if no acknowledgement is received within **3.0 seconds**.

### Rule:
- Always call `await interaction.response.defer(ephemeral=...)` at the very top of any command handler that performs database lookups, API queries, or file generation.
- After deferring, use `await interaction.followup.send(...)` instead of `response.send_message(...)`.

```python
@app_commands.command(name="my-command", description="Example command")
async def my_command(self, interaction: discord.Interaction):
    # 1. Immediately defer to avoid timeout
    await interaction.response.defer(ephemeral=True)

    try:
        # 2. Call service or database model
        result = await self.bot.flight_service.get_flight_info(...)
        
        # 3. Format presentation
        embed = self._create_result_embed(result)
        
        # 4. Respond via followup
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.exception("Failed to execute /my-command: %s", e)
        await interaction.followup.send("❌ An error occurred while processing your request.", ephemeral=True)
```

---

## 🔁 Scheduled Background Loops (`@tasks.loop`)

When running periodic tasks in Cogs:

1. **Always wait for the bot to be ready** before starting work:
   ```python
   @tasks.loop(minutes=5)
   async def monitor_task(self):
       # Process work
       pass

   @monitor_task.before_loop
   async def before_monitor_task(self):
       await self.bot.wait_until_ready()
   ```
2. **Handle errors inside the loop** so an uncaught exception does not kill the loop permanently:
   ```python
   @tasks.loop(minutes=5)
   async def safe_task(self):
       try:
           await self._run_monitoring_cycle()
       except Exception as e:
           logger.exception("Error in safe_task cycle: %s", e)
   ```
3. **Cancel loops in `cog_unload`**:
   ```python
   def cog_unload(self):
       self.safe_task.cancel()
   ```

---

## 🧩 Standard Cog Skeleton

Use this template when creating new Cogs:

```python
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger("oryxie.cogs.example")

class ExampleCog(commands.Cog):
    """
    Brief description of what this Cog is responsible for.
    Presentation layer only.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Start background tasks if any
        # self.background_loop.start()

    def cog_unload(self):
        # Clean up background tasks
        # self.background_loop.cancel()
        pass

    @app_commands.command(name="example", description="Example slash command")
    async def example_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        # Delegate to self.bot.<service> or self.bot.<model>
        await interaction.followup.send("Operation successful!")

async def setup(bot: commands.Bot):
    await bot.add_cog(ExampleCog(bot))
```
