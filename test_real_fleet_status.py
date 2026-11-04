"""Simple live test script for testing fleet status detection and deduplication with real data.

Usage:
    python test_real_fleet_status.py
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from database.manager import DatabaseManager
from database.if_live_tokens_model import IFLiveTokensModel
from database.stopped_flight_model import StoppedFlightModel
from database.flight_data import FlightData
from api.if_live_manager import IFLiveManager
from api.manager import InfiniteFlightAPIManager
from cogs.stopped_flight_monitor import (
    _aircraft_status_text,
    _status_schedule,
    _status_log_line,
    _crew_members,
    _crew_user_id,
)

sys.stdout.reconfigure(encoding="utf-8")


class DummyBot:
    def __init__(self, db):
        self.if_live_tokens_model = IFLiveTokensModel(db)
        self.if_api_manager = InfiniteFlightAPIManager(self)
        self.flightdata = FlightData()


async def main():
    org_id = os.getenv("IF_ORGANIZATION_ID", "").strip()
    if not org_id:
        print("[!] ERROR: IF_ORGANIZATION_ID is not configured in .env")
        return

    print("=" * 60)
    print("       ORYXIE REAL-DATA FLEET STATUS TEST")
    print("=" * 60)

    db = DatabaseManager(bot=None)
    await db.connect()
    bot = DummyBot(db)
    manager = IFLiveManager(bot)
    model = StoppedFlightModel(db)

    try:
        # 1. Fetch live fleet snapshot
        print("\n[*] Fetching live fleet snapshot from Infinite Flight API...")
        snapshot = await manager.get_live_fleet(org_id)
        fleet = getattr(snapshot, "fleet_list", None) or list(snapshot) or []
        positions = snapshot.to_positions_dict()
        schedules_by_aircraft = snapshot.schedules_by_aircraft
        previous_statuses = await model.get_aircraft_statuses()

        print(f"[+] Total aircraft in fleet: {len(fleet)}")
        print(f"[+] Cached DB status count: {len(previous_statuses)}")

        # 2. Check for duplicate registrations in fleet
        reg_counts = {}
        for ac in fleet:
            reg = str(getattr(ac, "registration", None) or "Unknown").strip().upper()
            reg_counts[reg] = reg_counts.get(reg, 0) + 1

        dupes = {r: c for r, c in reg_counts.items() if c > 1}
        if dupes:
            print(f"[!] WARNING: Duplicate registrations found in raw fleet: {dupes}")
        else:
            print("[+] OK: No duplicate registrations in raw fleet.")

        # 3. Calculate current statuses and test deduplication
        current_statuses = {}
        for ac in fleet:
            aid = str(getattr(ac, "id", "") or "")
            if not aid:
                continue
            reg = str(getattr(ac, "registration", "") or "Unknown")
            pos = positions.get(aid)
            st = _aircraft_status_text(
                bot,
                pos,
                schedules_by_aircraft.get(aid, []),
                previous_status=previous_statuses.get(aid),
            )
            current_statuses[aid] = (reg, st)

        # Deduplication
        deduped = {}
        reg_map = {}
        for aid, (reg, st) in current_statuses.items():
            norm = reg.replace("-", "").strip().upper()
            if norm not in reg_map:
                reg_map[norm] = aid
                deduped[aid] = (reg, st)
            else:
                prev_aid = reg_map[norm]
                _, prev_st = deduped[prev_aid]
                if st.startswith(("ENROUTE", "STOPPED")) and not prev_st.startswith(("ENROUTE", "STOPPED")):
                    del deduped[prev_aid]
                    reg_map[norm] = aid
                    deduped[aid] = (reg, st)

        print(f"[+] Deduplicated aircraft count: {len(deduped)}")

        # 4. Check status changes compared to DB
        changed = [
            (aid, reg, st)
            for aid, (reg, st) in deduped.items()
            if aid not in previous_statuses or previous_statuses[aid] != st
        ]

        print(f"\n[*] Status change check:")
        print(f"    - Aircraft with status changes: {len(changed)}")
        if changed:
            for aid, reg, st in changed:
                prev = previous_statuses.get(aid, "NONE (New)")
                print(f"    -> [CHANGED] {reg}: '{prev}' -> '{st}'")
        else:
            print("    -> [STABLE] No status changes detected (spam suppressed)!")

        # 5. Print fleet status overview
        print("\n" + "=" * 60)
        print("                 FLEET STATUS ROSTER")
        print("=" * 60)
        parked = 0
        enroute = 0
        stopped = 0
        for aid, (reg, st) in sorted(deduped.items(), key=lambda x: x[1][0]):
            pos = positions.get(aid)
            last_pilot = (pos.get("last_pilot_username") or "N/A") if pos else "N/A"
            if st.startswith("ENROUTE"):
                badge = "[ENROUTE] "
                enroute += 1
            elif st.startswith("STOPPED"):
                badge = "[STOPPED] "
                stopped += 1
            else:
                badge = "[PARKED]  "
                parked += 1

            print(f"{badge} {reg:8} | {st[:40]:40} | Pilot: {last_pilot}")

        print("=" * 60)
        print(f"Summary: {len(deduped)} Aircraft | {enroute} Enroute | {stopped} Stopped | {parked} Parked")
        print("=" * 60)

    finally:
        await manager.close()
        if hasattr(bot, "if_api_manager") and bot.if_api_manager:
            await bot.if_api_manager.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
