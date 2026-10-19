[1mdiff --git a/cogs/request_flight_cog.py b/cogs/request_flight_cog.py[m
[1mindex aa8b6a8..08f31a0 100644[m
[1m--- a/cogs/request_flight_cog.py[m
[1m+++ b/cogs/request_flight_cog.py[m
[36m@@ -1,4 +1,4 @@[m
[31m-"""Discord command for requesting the top scheduled route on Qatari Virtual aircraft.[m
[32m+[m[32m﻿"""Discord command for requesting the top scheduled route on Qatari Virtual aircraft.[m
 [m
 Copy this file to cogs/request_flight_cog.py. The sequence ordering assumption must[m
 be checked against the Infinite Flight organization page before enabling claims.[m
[36m@@ -16,11 +16,11 @@[m [mORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()[m
 [m
 RULES = ([m
     "**Qatari Virtual flight rules**\n"[m
[31m-    "• If an aircraft is stopped in the air, you have 24 hours to land.\n"[m
[31m-    "• If parked at a hub airport, you have 6 hours from assignment to take off.\n"[m
[31m-    "• If parked at a non-hub airport, you have 12 hours from assignment to take off.\n"[m
[31m-    "• You must fly the topmost scheduled route; you cannot skip lower routes.\n"[m
[31m-    "• If overdue, an admin may take over the flight or make the assignment available to other pilots."[m
[32m+[m[32m    "ΓÇó If an aircraft is stopped in the air, you have 24 hours to land.\n"[m
[32m+[m[32m    "ΓÇó If parked at a hub airport, you have 6 hours from assignment to take off.\n"[m
[32m+[m[32m    "ΓÇó If parked at a non-hub airport, you have 12 hours from assignment to take off.\n"[m
[32m+[m[32m    "ΓÇó You must fly the topmost scheduled route; you cannot skip lower routes.\n"[m
[32m+[m[32m    "ΓÇó If overdue, an admin may take over the flight or make the assignment available to other pilots."[m
 )[m
 [m
 [m
[36m@@ -48,7 +48,7 @@[m [mclass AircraftSelect(discord.ui.Select):[m
             )[m
             for a in aircraft[:25][m
         ][m
[31m-        super().__init__(placeholder="Choose an aircraft registration…", min_values=1, max_values=1, options=options)[m
[32m+[m[32m        super().__init__(placeholder="Choose an aircraft registrationΓÇª", min_values=1, max_values=1, options=options)[m
 [m
     async def callback(self, interaction: discord.Interaction):[m
         aircraft = self.aircraft_by_id.get(self.values[0])[m
[36m@@ -90,7 +90,7 @@[m [mclass ClaimRouteView(discord.ui.View):[m
         for child in self.children:[m
             child.disabled = True[m
         await interaction.response.edit_message(view=self)[m
[31m-        await interaction.followup.send("Checking the schedule again before assigning…", ephemeral=True)[m
[32m+[m[32m        await interaction.followup.send("Checking the schedule again before assigningΓÇª", ephemeral=True)[m
 [m
         try:[m
             manager = getattr(self.cog.bot, "if_live_manager", None)[m
[36m@@ -127,14 +127,14 @@[m [mclass ClaimRouteView(discord.ui.View):[m
                     await interaction.followup.send("The API did not confirm your assignment. Please contact staff before trying again.", ephemeral=True)[m
                     return[m
                 await interaction.followup.send([m
[31m-                    f"✅ **Assignment confirmed**\nAircraft: **{_get(self.aircraft, 'registration', 'Unknown')}**\n"[m
[31m-                    f"Route: **{_get(top, 'callsign', 'Unknown')}** — {_get(top, 'origin_icao', '???')} → {_get(top, 'destination_icao', '???')}\n"[m
[32m+[m[32m                    f"Γ£à **Assignment confirmed**\nAircraft: **{_get(self.aircraft, 'registration', 'Unknown')}**\n"[m
[32m+[m[32m                    f"Route: **{_get(top, 'callsign', 'Unknown')}** ΓÇö {_get(top, 'origin_icao', '???')} ΓåÆ {_get(top, 'destination_icao', '???')}\n"[m
                     f"Pilot: **{self.pilot.get('callsign', interaction.user.display_name)}**\n\n{RULES}",[m
                     ephemeral=True,[m
                 )[m
         except Exception:[m
             log.exception("Failed to assign Infinite Flight schedule crew")[m
[31m-            await interaction.followup.send("❌ I couldn't verify the assignment with Infinite Flight. No success is being reported; please contact staff and don't retry until they check the schedule.", ephemeral=True)[m
[32m+[m[32m            await interaction.followup.send("Γ¥î I couldn't verify the assignment with Infinite Flight. No success is being reported; please contact staff and don't retry until they check the schedule.", ephemeral=True)[m
 [m
     @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)[m
     async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):[m
[36m@@ -194,19 +194,19 @@[m [mclass RequestFlightCog(commands.Cog):[m
             schedules = sorted(schedules, key=lambda s: int(_get(s, "sequence", 0)))[m
             top = schedules[0][m
             crew = _get(top, "crew", []) or [][m
[31m-            route = f"**{_get(top, 'callsign', 'Unknown')}** — {_get(top, 'origin_icao', '???')} → {_get(top, 'destination_icao', '???')}"[m
[32m+[m[32m            route = f"**{_get(top, 'callsign', 'Unknown')}** ΓÇö {_get(top, 'origin_icao', '???')} ΓåÆ {_get(top, 'destination_icao', '???')}"[m
             state = _get(position, "state", "unknown")[m
             ground = _get(position, "is_on_ground", None)[m
[31m-            status = f"Aircraft state: `{state}` · " + ("On ground" if ground is True else "In flight" if ground is False else "Ground status unknown")[m
[32m+[m[32m            status = f"Aircraft state: `{state}` ┬╖ " + ("On ground" if ground is True else "In flight" if ground is False else "Ground status unknown")[m
             if crew:[m
                 names = [str(_get(m, "username", _get(m, "userName", _crew_user_id(m)))) for m in crew][m
[31m-                embed = discord.Embed(title=f"{_get(aircraft, 'registration', 'Aircraft')} · Top scheduled route", description=route, color=discord.Color.orange())[m
[32m+[m[32m                embed = discord.Embed(title=f"{_get(aircraft, 'registration', 'Aircraft')} ┬╖ Top scheduled route", description=route, color=discord.Color.orange())[m
                 embed.add_field(name="Assignment", value="Already assigned: " + ", ".join(names), inline=False)[m
                 embed.add_field(name="Aircraft status", value=status, inline=False)[m
                 embed.set_footer(text="This route is not available to claim while assigned.")[m
                 await interaction.followup.send(embed=embed, ephemeral=True)[m
                 return[m
[31m-            embed = discord.Embed(title=f"Request flight · {_get(aircraft, 'registration', 'Aircraft')}", description=route, color=discord.Color.blue())[m
[32m+[m[32m            embed = discord.Embed(title=f"Request flight ┬╖ {_get(aircraft, 'registration', 'Aircraft')}", description=route, color=discord.Color.blue())[m
             embed.add_field(name="Aircraft status", value=status, inline=False)[m
             embed.add_field(name="Rules acknowledgement", value=RULES, inline=False)[m
             embed.set_footer(text="Confirm only if you understand and agree to these rules.")[m
