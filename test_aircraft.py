
import asyncio
import os
from dotenv import load_dotenv

from database.manager import DatabaseManager
from database.if_live_tokens_model import IFLiveTokensModel
from api.if_live_manager import IFLiveManager

load_dotenv()


async def main():
    db = DatabaseManager(None)
    live_manager = None

    try:
        print("Connecting to database...")
        await db.connect()

        class DiagnosticBot:
            pass

        bot = DiagnosticBot()
        bot.if_live_tokens_model = IFLiveTokensModel(db)
        live_manager = IFLiveManager(bot)

        org_id = os.getenv("IF_ORGANIZATION_ID")
        if not org_id:
            print("ERROR: IF_ORGANIZATION_ID is missing.")
            return

        print("Connecting to Infinite Flight Live...")
        async with await live_manager.get_client(1) as client:
            aircraft = await client.list_organization_aircraft(org_id)

            if not aircraft:
                print("No organization aircraft returned.")
                return

            print(f"Found {len(aircraft)} aircraft.")
            seen_ids = set()

            print("\n--- RAW AIRCRAFT MODEL FIELDS ---")

            for item in aircraft:
                aircraft_id = str(item.id)

                if aircraft_id in seen_ids:
                    continue
                seen_ids.add(aircraft_id)

                try:
                    raw = await client._request(
                        "GET", f"/live/aircraft/{aircraft_id}"
                    )

                    if isinstance(raw, dict):
                        model_fields = {
                            key: value
                            for key, value in raw.items()
                            if any(
                                word in key.lower()
                                for word in (
                                    "model", "name", "type", "aircraftid",
                                    "registration", "title"
                                )
                            )
                        }
                        print(
                            f"\nRegistration: {item.registration}"
                            f"\nAircraft details: {model_fields}"
                        )
                    else:
                        print(
                            f"\nRegistration: {item.registration}"
                            f"\nUnexpected response type: {type(raw).__name__}"
                        )

                except Exception as e:
                    print(
                        f"\nRegistration: {item.registration}"
                        f"\nLookup failed: {type(e).__name__}: {e}"
                    )

            print("\n--- END DIAGNOSTIC ---")

    except Exception as e:
        print(f"Diagnostic failed: {type(e).__name__}: {e}")

    finally:
        if live_manager:
            await live_manager.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
