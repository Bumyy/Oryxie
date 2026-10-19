[1mdiff --git a/cogs/request_flight_cog.py b/cogs/request_flight_cog.py[m
[1mindex aa8b6a8..a8d83ab 100644[m
[1m--- a/cogs/request_flight_cog.py[m
[1m+++ b/cogs/request_flight_cog.py[m
[36m@@ -1,11 +1,10 @@[m
[31m-"""Discord command for requesting the top scheduled route on Qatari Virtual aircraft.[m
 [m
[31m-Copy this file to cogs/request_flight_cog.py. The sequence ordering assumption must[m
[31m-be checked against the Infinite Flight organization page before enabling claims.[m
[31m-"""[m
[32m+[m[32m"""Discord command for requesting the top scheduled route on Qatari Virtual aircraft."""[m
 [m
[31m-import os[m
[32m+[m[32mimport asyncio[m
 import logging[m
[32m+[m[32mimport os[m
[32m+[m[32mimport time[m
 [m
 import discord[m
 from discord import app_commands[m
[36m@@ -13,55 +12,388 @@[m [mfrom discord.ext import commands[m
 [m
 log = logging.getLogger(__name__)[m
 ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()[m
[32m+[m[32mTOKEN_OWNER_ID = 1[m
[32m+[m
[32m+[m[32m# Fleet registrations change rarely; keep this cache warm to avoid a Live API call[m
[32m+[m[32m# every time someone opens the command.[m
[32m+[m[32mFLEET_CACHE_SECONDS = 300[m
[32m+[m
[32m+[m[32m# Retired aircraft are excluded from all request-flight workflows.[m
[32m+[m[32mRETIRED_AIRCRAFT_REGISTRATIONS = {"A7BFC", "A7BFA"}[m
[32m+[m
[32m+[m[32m# Verified registration -> aircraft model mapping supplied by Qatari Virtual.[m
[32m+[m[32m# Add new active aircraft here when the fleet changes.[m
[32m+[m[32mAIRCRAFT_MODELS = {[m
[32m+[m[32m    "A7-ANN": "A350-1000",[m
[32m+[m[32m    "A7-APC": "A380-800",[m
[32m+[m[32m    "A7-BAE": "777-300ER",[m
[32m+[m[32m    "A7-ALR": "A350-900",[m
[32m+[m[32m    "A7-DAC": "A350-900",[m
[32m+[m[32m    "A7-BCA": "787-8",[m
[32m+[m[32m    "A7BBA": "777-200LR",[m
[32m+[m[32m    "A7BAG": "777-300ER",[m
[32m+[m[32m    "A7-ANG": "A350-1000",[m
[32m+[m[32m    "A7BCC": "787-8",[m
[32m+[m[32m    "A7DAB": "A350-900",[m
[32m+[m[32m    "A7-AOB": "A350-1000",[m
[32m+[m[32m    "A7BAF": "777-300ER",[m
[32m+[m[32m    "A7BAB": "777-300ER",[m
[32m+[m[32m    "A7BCB": "787-8",[m
[32m+[m[32m    "A7DAA": "A350-900",[m
[32m+[m[32m    "A7AED": "A330-300",[m
[32m+[m[32m    "A7-BAH": "777-300ER",[m
[32m+[m[32m    "A7APD": "A380-800",[m
[32m+[m[32m    "A7-BBB": "777-200LR",[m
[32m+[m[32m    "A7AEE": "A330-300",[m
[32m+[m[32m    "A7-BCD": "787-8",[m
[32m+[m[32m    "A7ADS": "A321-200",[m
[32m+[m[32m    "A7-AOA": "A350-1000",[m
[32m+[m[32m    "A7-AOH": "A350-1000",[m
[32m+[m[32m    "A7-BAA": "777-300ER",[m
[32m+[m[32m    "B-LRO": "A350-900",[m
[32m+[m[32m    "EC-ORE": "A321neo",[m
[32m+[m[32m    "A7-BAC": "777-300ER",[m
[32m+[m[32m    "A7-BAI": "777-300ER",[m
[32m+[m[32m    "N302NY": "A321neo",[m
[32m+[m[32m    "N762AN": "777-200ER",[m
[32m+[m[32m    "VH-ZNF": "787-9",[m
[32m+[m[32m    "IF-FXQK": "777-300ER",[m
[32m+[m[32m    "IF-MPSK": "A321neo",[m
[32m+[m[32m    "IF-SKJK": "777-300ER",[m
[32m+[m[32m    "IF-KSKI": "787-9",[m
[32m+[m[32m}[m
 [m
 RULES = ([m
     "**Qatari Virtual flight rules**\n"[m
     "• If an aircraft is stopped in the air, you have 24 hours to land.\n"[m
     "• If parked at a hub airport, you have 6 hours from assignment to take off.\n"[m
     "• If parked at a non-hub airport, you have 12 hours from assignment to take off.\n"[m
[31m-    "• You must fly the topmost scheduled route; you cannot skip lower routes.\n"[m
     "• If overdue, an admin may take over the flight or make the assignment available to other pilots."[m
 )[m
 [m
 [m
 def _get(obj, key, default=None):[m
[31m-    """Read a value from either a dataclass/object or a dictionary."""[m
     if isinstance(obj, dict):[m
         return obj.get(key, default)[m
     return getattr(obj, key, default)[m
 [m
 [m
[32m+[m[32mdef _normalized_registration(aircraft):[m
[32m+[m[32m    registration = str(_get(aircraft, "registration", "") or "")[m
[32m+[m[32m    return registration.strip().upper().replace("-", "")[m
[32m+[m
[32m+[m
[32m+[m[32mdef _is_retired_aircraft(aircraft):[m
[32m+[m[32m    return _normalized_registration(aircraft) in RETIRED_AIRCRAFT_REGISTRATIONS[m
[32m+[m
[32m+[m
 def _crew_user_id(member):[m
[31m-    return str(_get(member, "userId", _get(member, "user_id", _get(member, "id", ""))))[m
[32m+[m[32m    for key in ("userId", "user_id", "ifuserid", "id"):[m
[32m+[m[32m        value = _get(member, key)[m
[32m+[m[32m        if value not in (None, ""):[m
[32m+[m[32m            return str(value)[m
[32m+[m[32m    return ""[m
[32m+[m
[32m+[m
[32m+[m[32mdef _aircraft_model(aircraft):[m
[32m+[m[32m    for key in ([m
[32m+[m[32m        "model", "aircraft_name", "aircraftName", "name",[m
[32m+[m[32m        "title", "type_name", "typeName",[m
[32m+[m[32m    ):[m
[32m+[m[32m        value = _get(aircraft, key)[m
[32m+[m[32m        if value and str(value).strip():[m
[32m+[m[32m            return str(value).strip()[m
[32m+[m
[32m+[m[32m    registration = str(_get(aircraft, "registration", "") or "").strip().upper()[m
[32m+[m[32m    return AIRCRAFT_MODELS.get(registration, "")[m
[32m+[m
[32m+[m
[32m+[m[32mdef _aircraft_category(aircraft):[m
[32m+[m[32m    model = _aircraft_model(aircraft).lower().replace(" ", "")[m
[32m+[m[32m    if model.startswith("a") and len(model) > 1 and model[1].isdigit():[m
[32m+[m[32m        return "Airbus"[m
[32m+[m[32m    if model.startswith(("777", "787", "737", "747", "757", "767", "717", "727")):[m
[32m+[m[32m        return "Boeing"[m
[32m+[m[32m    return "Other"[m
[32m+[m
[32m+[m
[32m+[m[32mdef _model_label(aircraft):[m
[32m+[m[32m    return _aircraft_model(aircraft) or "Model unknown"[m
[32m+[m
[32m+[m
[32m+[m[32mdef _state_label(position):[m
[32m+[m[32m    labels = {[m
[32m+[m[32m        0: "Unknown",[m
[32m+[m[32m        1: "On ground",[m
[32m+[m[32m        2: "In flight",[m
[32m+[m[32m        3: "Cancelled",[m
[32m+[m[32m        4: "Stopped",[m
[32m+[m[32m        5: "Maintenance",[m
[32m+[m[32m    }[m
[32m+[m[32m    raw = _get(position, "state", None)[m
[32m+[m[32m    try:[m
[32m+[m[32m        return labels.get(int(raw), f"Unknown ({raw})")[m
[32m+[m[32m    except (TypeError, ValueError):[m
[32m+[m[32m        return str(raw if raw is not None else "Unknown")[m
[32m+[m
[32m+[m
[32m+[m[32mdef _route_text(schedule):[m
[32m+[m[32m    callsign = _get(schedule, "callsign") or "Callsign unavailable from API"[m
[32m+[m[32m    origin = _get(schedule, "origin_icao") or "???"[m
[32m+[m[32m    destination = _get(schedule, "destination_icao") or "???"[m
[32m+[m[32m    return f"**{callsign}** — {origin} → {destination}"[m
[32m+[m
[32m+[m
[32m+[m[32mdef _sequence(schedule):[m
[32m+[m[32m    try:[m
[32m+[m[32m        return int(_get(schedule, "sequence", 0))[m
[32m+[m[32m    except (TypeError, ValueError):[m
[32m+[m[32m        return 0[m
[32m+[m
[32m+[m
[32m+[m[32mdef _sorted_schedules(schedules):[m
[32m+[m[32m    return sorted(schedules or [], key=_sequence)[m
[32m+[m
[32m+[m
[32m+[m[32mdef _route_status_number(schedule):[m
[32m+[m[32m    try:[m
[32m+[m[32m        return int(_get(schedule, "status", None))[m
[32m+[m[32m    except (TypeError, ValueError):[m
[32m+[m[32m        return None[m
[32m+[m
[32m+[m
[32m+[m[32mdef _is_unavailable_status(schedule):[m
[32m+[m[32m    # Infinite Flight status values used here: CANCELLED=9, ARRIVED=11.[m
[32m+[m[32m    return _route_status_number(schedule) in {9, 11}[m
 [m
 [m
[31m-class AircraftSelect(discord.ui.Select):[m
[32m+[m[32mdef _active_schedules(schedules):[m
[32m+[m[32m    """Deduplicate repeated schedule IDs, then remove cancelled/arrived history rows.[m
[32m+[m
[32m+[m[32m    Some Live API responses can repeat the same schedule ID with different status[m
[32m+[m[32m    snapshots. A terminal status wins so a completed route is not accidentally[m
[32m+[m[32m    resurrected by an older scheduled snapshot.[m
[32m+[m[32m    """[m
[32m+[m[32m    by_id = {}[m
[32m+[m[32m    no_id = [][m
[32m+[m
[32m+[m[32m    for route in _sorted_schedules(schedules):[m
[32m+[m[32m        route_id = _get(route, "id")[m
[32m+[m[32m        if route_id in (None, ""):[m
[32m+[m[32m            no_id.append(route)[m
[32m+[m[32m            continue[m
[32m+[m
[32m+[m[32m        key = str(route_id)[m
[32m+[m[32m        previous = by_id.get(key)[m
[32m+[m
[32m+[m[32m        if previous is None:[m
[32m+[m[32m            by_id[key] = route[m
[32m+[m[32m            continue[m
[32m+[m
[32m+[m[32m        prev_terminal = _is_unavailable_status(previous)[m
[32m+[m[32m        this_terminal = _is_unavailable_status(route)[m
[32m+[m
[32m+[m[32m        if this_terminal and not prev_terminal:[m
[32m+[m[32m            by_id[key] = route[m
[32m+[m[32m        elif this_terminal == prev_terminal:[m
[32m+[m[32m            # Prefer the snapshot with crew data, otherwise keep the earlier row.[m
[32m+[m[32m            if not (_get(previous, "crew", []) or []) and (_get(route, "crew", []) or []):[m
[32m+[m[32m                by_id[key] = route[m
[32m+[m
[32m+[m[32m    unique = list(by_id.values()) + no_id[m
[32m+[m[32m    return [[m
[32m+[m[32m        route for route in _sorted_schedules(unique)[m
[32m+[m[32m        if not _is_unavailable_status(route)[m
[32m+[m[32m    ][m
[32m+[m
[32m+[m
[32m+[m[32mdef _next_claimable_index(schedules):[m
[32m+[m[32m    """Find the first unassigned route after any leading routes already crewed."""[m
[32m+[m[32m    for index, route in enumerate(schedules):[m
[32m+[m[32m        if not (_get(route, "crew", []) or []):[m
[32m+[m[32m            return index[m
[32m+[m[32m    return None[m
[32m+[m
[32m+[m
[32m+[m[32mdef _reciprocal_routes(first, second):[m
[32m+[m[32m    """True when second is the immediately following reciprocal route."""[m
[32m+[m[32m    first_origin = str(_get(first, "origin_icao", "") or "").upper()[m
[32m+[m[32m    first_dest = str(_get(first, "destination_icao", "") or "").upper()[m
[32m+[m[32m    second_origin = str(_get(second, "origin_icao", "") or "").upper()[m
[32m+[m[32m    second_dest = str(_get(second, "destination_icao", "") or "").upper()[m
[32m+[m
[32m+[m[32m    return bool([m
[32m+[m[32m        first_origin and first_dest and second_origin and second_dest[m
[32m+[m[32m        and first_origin == second_dest[m
[32m+[m[32m        and first_dest == second_origin[m
[32m+[m[32m    )[m
[32m+[m
[32m+[m
[32m+[m[32mclass AircraftCategorySelect(discord.ui.Select):[m
     def __init__(self, cog, pilot, aircraft):[m
[31m-        self.cog = cog[m
[31m-        self.pilot = pilot[m
[31m-        self.aircraft_by_id = {str(_get(a, "id")): a for a in aircraft}[m
[32m+[m[32m        self.cog, self.pilot, self.aircraft = cog, pilot, aircraft[m
[32m+[m
[32m+[m[32m        categories = [[m
[32m+[m[32m            name for name in ("Airbus", "Boeing", "Other")[m
[32m+[m[32m            if any(_aircraft_category(a) == name for a in aircraft)[m
[32m+[m[32m        ][m
[32m+[m
         options = [[m
             discord.SelectOption([m
[31m-                label=str(_get(a, "registration", "Unknown registration"))[:100],[m
[31m-                description=f"Aircraft status: {_get(a, 'status', 'unknown')}"[:100],[m
[31m-                value=str(_get(a, "id")),[m
[32m+[m[32m                label=name,[m
[32m+[m[32m                description=f"{sum(_aircraft_category(a) == name for a in aircraft)} aircraft",[m
             )[m
[31m-            for a in aircraft[:25][m
[32m+[m[32m            for name in categories[m
         ][m
[31m-        super().__init__(placeholder="Choose an aircraft registration…", min_values=1, max_values=1, options=options)[m
[32m+[m
[32m+[m[32m        super().__init__([m
[32m+[m[32m            placeholder="Choose aircraft type…",[m
[32m+[m[32m            min_values=1,[m
[32m+[m[32m            max_values=1,[m
[32m+[m[32m            options=options,[m
[32m+[m[32m        )[m
 [m
     async def callback(self, interaction: discord.Interaction):[m
[31m-        aircraft = self.aircraft_by_id.get(self.values[0])[m
[31m-        if aircraft is None:[m
[31m-            await interaction.response.send_message("I couldn't find that aircraft. Run `/request-flight` again.", ephemeral=True)[m
[32m+[m[32m        if str(interaction.user.id) != str(self.pilot.get("discordid")):[m
[32m+[m[32m            await interaction.response.send_message([m
[32m+[m[32m                "Only the pilot who requested this list can use it.",[m
[32m+[m[32m                ephemeral=True,[m
[32m+[m[32m            )[m
             return[m
[32m+[m
[32m+[m[32m        category = self.values[0][m
[32m+[m[32m        selected = [[m
[32m+[m[32m            aircraft for aircraft in self.aircraft[m
[32m+[m[32m            if _aircraft_category(aircraft) == category[m
[32m+[m[32m            and not _is_retired_aircraft(aircraft)[m
[32m+[m[32m        ][m
[32m+[m
[32m+[m[32m        await interaction.response.edit_message([m
[32m+[m[32m            content=f"**{category} aircraft** — choose a registration. The model is shown in each option.",[m
[32m+[m[32m            view=AircraftListView(self.cog, self.pilot, selected, category, 0),[m
[32m+[m[32m        )[m
[32m+[m
[32m+[m
[32m+[m[32mclass AircraftListSelect(discord.ui.Select):[m
[32m+[m[32m    def __init__(self, cog, pilot, aircraft, category, page):[m
[32m+[m[32m        self.cog, self.pilot, self.aircraft, self.category, self.page = ([m
[32m+[m[32m            cog, pilot, aircraft, category, page[m
[32m+[m[32m        )[m
[32m+[m
[32m+[m[32m        start = page * 25[m
[32m+[m[32m        page_aircraft = aircraft[start:start + 25][m
[32m+[m[32m        options = [][m
[32m+[m
[32m+[m[32m        for item in page_aircraft:[m
[32m+[m[32m            registration = str(_get(item, "registration") or "Unknown registration")[m
[32m+[m[32m            options.append([m
[32m+[m[32m                discord.SelectOption([m
[32m+[m[32m                    label=registration[:100],[m
[32m+[m[32m                    description=_model_label(item)[:100],[m
[32m+[m[32m                    value=str(_get(item, "id")),[m
[32m+[m[32m                )[m
[32m+[m[32m            )[m
[32m+[m
[32m+[m[32m        pages = max(1, (len(aircraft) + 24) // 25)[m
[32m+[m
[32m+[m[32m        super().__init__([m
[32m+[m[32m            placeholder=f"Choose {category} registration (page {page + 1}/{pages})…",[m
[32m+[m[32m            min_values=1,[m
[32m+[m[32m            max_values=1,[m
[32m+[m[32m            options=options,[m
[32m+[m[32m        )[m
[32m+[m
[32m+[m[32m    async def callback(self, interaction: discord.Interaction):[m
[32m+[m[32m        if str(interaction.user.id) != str(self.pilot.get("discordid")):[m
[32m+[m[32m            await interaction.response.send_message([m
[32m+[m[32m                "Only the pilot who requested this list can use it.",[m
[32m+[m[32m                ephemeral=True,[m
[32m+[m[32m            )[m
[32m+[m[32m            return[m
[32m+[m
[32m+[m[32m        aircraft = next([m
[32m+[m[32m            ([m
[32m+[m[32m                item for item in self.aircraft[m
[32m+[m[32m                if str(_get(item, "id")) == self.values[0][m
[32m+[m[32m            ),[m
[32m+[m[32m            None,[m
[32m+[m[32m        )[m
[32m+[m
[32m+[m[32m        if aircraft is None or _is_retired_aircraft(aircraft):[m
[32m+[m[32m            await interaction.response.send_message([m
[32m+[m[32m                "That aircraft is unavailable or has been retired. Run `/request-flight` again.",[m
[32m+[m[32m                ephemeral=True,[m
[32m+[m[32m            )[m
[32m+[m[32m            return[m
[32m+[m
         await self.cog.show_aircraft_route(interaction, self.pilot, aircraft)[m
 [m
 [m
[32m+[m[32mclass AircraftListView(discord.ui.View):[m
[32m+[m[32m    def __init__(self, cog, pilot, aircraft, category, page=0):[m
[32m+[m[32m        super().__init__(timeout=180)[m
[32m+[m
[32m+[m[32m        self.add_item(AircraftListSelect(cog, pilot, aircraft, category, page))[m
[32m+[m
[32m+[m[32m        if page > 0:[m
[32m+[m[32m            self.add_item([m
[32m+[m[32m                AircraftPageButton(cog, pilot, aircraft, category, page - 1, "Previous")[m
[32m+[m[32m            )[m
[32m+[m
[32m+[m[32m        if (page + 1) * 25 < len(aircraft):[m
[32m+[m[32m            self.add_item([m
[32m+[m[32m                AircraftPageButton(cog, pilot, aircraft, category, page + 1, "Next")[m
[32m+[m[32m            )[m
[32m+[m
[32m+[m[32m        self.add_item([m
[32m+[m[32m            AircraftPageButton([m
[32m+[m[32m                cog, pilot, aircraft, category, 0, "Back to categories", back=True[m
[32m+[m[32m            )[m
[32m+[m[32m        )[m
[32m+[m
[32m+[m
[32m+[m[32mclass AircraftPageButton(discord.ui.Button):[m
[32m+[m[32m    def __init__(self, cog, pilot, aircraft, category, page, label, back=False):[m
[32m+[m[32m        super().__init__(label=label, style=discord.ButtonStyle.secondary)[m
[32m+[m[32m        self.cog, self.pilot, self.aircraft, self.category, self.page, self.back = ([m
[32m+[m[32m            cog, pilot, aircraft, category, page, back[m
[32m+[m[32m        )[m
[32m+[m
[32m+[m[32m    async def callback(self, interaction: discord.Interaction):[m
[32m+[m[32m        if str(interaction.user.id) != str(self.pilot.get("discordid")):[m
[32m+[m[32m            await interaction.response.send_message([m
[32m+[m[32m                "Only the pilot who requested this list can use it.",[m
[32m+[m[32m                ephemeral=True,[m
[32m+[m[32m            )[m
[32m+[m[32m            return[m
[32m+[m
[32m+[m[32m        if self.back:[m
[32m+[m[32m            active_aircraft = [[m
[32m+[m[32m                aircraft for aircraft in (self.cog._aircraft_cache or self.aircraft)[m
[32m+[m[32m                if not _is_retired_aircraft(aircraft)[m
[32m+[m[32m            ][m
[32m+[m
[32m+[m[32m            await interaction.response.edit_message([m
[32m+[m[32m                content="Choose an aircraft category:",[m
[32m+[m[32m                view=AircraftSelectView(self.cog, self.pilot, active_aircraft),[m
[32m+[m[32m            )[m
[32m+[m[32m        else:[m
[32m+[m[32m            await interaction.response.edit_message([m
[32m+[m[32m                content=f"**{self.category} aircraft** — choose a registration:",[m
[32m+[m[32m                view=AircraftListView([m
[32m+[m[32m                    self.cog, self.pilot, self.aircraft, self.category, self.page[m
[32m+[m[32m                ),[m
[32m+[m[32m            )[m
[32m+[m
[32m+[m
 class AircraftSelectView(discord.ui.View):[m
     def __init__(self, cog, pilot, aircraft):[m
[31m-        super().__init__(timeout=120)[m
[31m-        self.add_item(AircraftSelect(cog, pilot, aircraft))[m
[32m+[m[32m        super().__init__(timeout=180)[m
[32m+[m[32m        active_aircraft = [[m
[32m+[m[32m            item for item in aircraft if not _is_retired_aircraft(item)[m
[32m+[m[32m        ][m
[32m+[m[32m        if active_aircraft:[m
[32m+[m[32m            self.add_item(AircraftCategorySelect(cog, pilot, active_aircraft))[m
 [m
 [m
 class ClaimRouteView(discord.ui.View):[m
[36m@@ -72,149 +404,506 @@[m [mclass ClaimRouteView(discord.ui.View):[m
         self.aircraft = aircraft[m
         self.schedule = schedule[m
         self.pilot_if_user_id = str(pilot_if_user_id)[m
[31m-        self.message = None