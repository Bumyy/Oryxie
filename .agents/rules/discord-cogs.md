# Discord Cogs Rules & Guidelines

Guidelines for building and editing Cogs in `cogs/`:

1. **Class Structure & Naming**:
   - Every Cog class must inherit from `commands.Cog`.
   - Provide a top-level `async def setup(bot: commands.Bot): await bot.add_cog(CogName(bot))` function at the bottom of the file.
   - Use descriptive names matching the domain (e.g. `RequestFlightCog`, `StoppedFlightMonitor`).

2. **Discord 3-Second Timeout**:
   - Slash commands fail if not acknowledged within 3 seconds.
   - Always call `await interaction.response.defer(ephemeral=...)` before any database queries, external API calls, or PDF rendering.
   - Send replies using `await interaction.followup.send(...)` after deferring.

3. **Background Loops (`@tasks.loop`)**:
   - Always guard startup with `@<loop_name>.before_loop: await self.bot.wait_until_ready()`.
   - Wrap the loop body in `try...except Exception:` with `logger.exception()` to ensure an unhandled error does not silently terminate the task.
   - Cancel loops in `cog_unload()`.

4. **Dependency Access**:
   - Access database models and services through `self.bot`:
     - Models: `self.bot.pilots_model`, `self.bot.routes_model`, `self.bot.pireps_model`, etc.
     - Services: `self.bot.flight_service`, `self.bot.pdf_service`, `self.bot.fleet_service`, etc.
     - APIs: `self.bot.if_api_manager`, `self.bot.cc_api_manager`, `self.bot.if_live_manager`.
