import airportsdata

class FlightService:
    def __init__(self):
        self.airports = airportsdata.load('ICAO')

    def get_flight_direction(self, departure_icao: str, arrival_icao: str) -> str:
        """
        Calculates the flight direction (East or West) based on the shortest path.
        Returns 'East', 'West', or 'N/A' for primarily North/South routes.
        """
        try:
            # Get longitude for both airports from the library
            dep_lon = self.airports[departure_icao.upper()]['lon']
            arr_lon = self.airports[arrival_icao.upper()]['lon']
        except (KeyError, TypeError):
            # This handles cases where the airport ICAO is invalid
            raise ValueError("Invalid departure or arrival ICAO code provided.")

        # --- Direction Logic ---
        diff = arr_lon - dep_lon

        # Handle crossing the International Date Line
        if abs(diff) > 180:
            if diff > 0:
                # e.g., Tokyo (140) to LA (-118) -> diff is -258 (West)
                # We know the short way is East
                diff = diff - 360 
            else:
                # e.g., LA (-118) to Tokyo (140) -> diff is 258 (East)
                # We know the short way is West
                diff = diff + 360
        
        # Determine final direction with a North/South threshold
        if diff > 15:  # Strong Eastbound tendency
            return "east"
        elif diff < -15: # Strong Westbound tendency
            return "west"
        else: # Primarily a North/South flight
            return "N/A"