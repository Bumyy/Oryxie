from typing import Optional, Dict, List
import csv
import os
import logging

class OwdRouteModel:
    """
    Handles all operations related to the OWD routes CSV file.
    """
    def __init__(self, db_manager=None):
        self.csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets', 'QRV oneworld Discover CSV - Sheet1.csv')
        self._routes_cache = None

    def _load_routes(self) -> List[Dict]:
        """Load routes from CSV file and cache them."""
        if self._routes_cache is not None:
            return self._routes_cache
        
        logging.info(f"[DEBUG] Loading OWD Routes from {self.csv_path}")
        if not os.path.exists(self.csv_path):
             logging.error(f"[DEBUG] CSV FILE NOT FOUND AT {self.csv_path}")
             # Debug: List files in the directory to check for typos
             directory = os.path.dirname(self.csv_path)
             if os.path.exists(directory):
                 logging.error(f"[DEBUG] Files actually present in {directory}: {os.listdir(directory)}")
             else:
                 logging.error(f"[DEBUG] Directory does not exist: {directory}")
             return []

        routes = []
        with open(self.csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                routes.append(row)
        
        logging.info(f"[DEBUG] Loaded {len(routes)} routes from CSV.")
        if len(routes) > 0:
             logging.info(f"[DEBUG] Sample Route Key: {list(routes[0].keys())}")

        self._routes_cache = routes
        return routes

    async def find_route_by_icao(self, dep_icao: str, arr_icao: str) -> Optional[Dict]:
        """
        Finds a route in the OWD CSV matching a specific departure and arrival ICAO.

        Args:
            dep_icao: The departure airport ICAO code.
            arr_icao: The arrival airport ICAO code.

        Returns:
            A dictionary containing route details.
            Returns None if no route is found.
        """
        routes = self._load_routes()
        
        for route in routes:
            if route['Departure'] == dep_icao and route['Arrival'] == arr_icao:
                return {
                    "flight_number": route['Flight Number'],
                    "departure": route['Departure'],
                    "arrival": route['Arrival'],
                    "aircraft": route['Aircraft'],
                    "flight_time": route['Flight Time'],
                    "airline": route['Airline']
                }
        
        return None

    async def find_route_by_flight_number(self, flight_number: str) -> Optional[Dict]:
        """
        Finds a route in the OWD CSV matching a specific flight number.

        Args:
            flight_number: The flight number to search for.

        Returns:
            A dictionary containing route details.
            Returns None if no route is found.
        """
        routes = self._load_routes()
        logging.info(f"[DEBUG] Searching OWD for flight number: '{flight_number}'")
        
        target_flight = flight_number.strip().upper()
        
        for route in routes:
            if route['Flight Number'].strip().upper() == target_flight:
                logging.info(f"[DEBUG] Found OWD route: {route}")
                return {
                    "flight_number": route['Flight Number'],
                    "departure": route['Departure'],
                    "arrival": route['Arrival'],
                    "aircraft": route['Aircraft'],
                    "flight_time": route['Flight Time'],
                    "airline": route['Airline']
                }
        
        return None

    async def find_routes_by_airline(self, airline: str) -> List[Dict]:
        """
        Finds all routes for a specific airline.
        
        Args:
            airline: The airline name to search for.
        
        Returns:
            A list of dictionaries containing route details.
        """
        routes = self._load_routes()
        results = []
        
        for route in routes:
            if route['Airline'] == airline:
                results.append({
                    "flight_number": route['Flight Number'],
                    "departure": route['Departure'],
                    "arrival": route['Arrival'],
                    "aircraft": route['Aircraft'],
                    "flight_time": route['Flight Time'],
                    "airline": route['Airline']
                })
        
        return results

    async def find_routes_from_airport(self, dep_icao: str) -> List[Dict]:
        """
        Finds all routes departing from a specific airport.
        
        Args:
            dep_icao: The departure airport ICAO code.
        
        Returns:
            A list of dictionaries containing route details.
        """
        routes = self._load_routes()
        results = []
        
        for route in routes:
            if route['Departure'] == dep_icao:
                results.append({
                    "flight_number": route['Flight Number'],
                    "departure": route['Departure'],
                    "arrival": route['Arrival'],
                    "aircraft": route['Aircraft'],
                    "flight_time": route['Flight Time'],
                    "airline": route['Airline']
                })
        
        return results

    async def find_routes_to_airport(self, arr_icao: str) -> List[Dict]:
        """
        Finds all routes going to a specific airport.
        
        Args:
            arr_icao: The arrival airport ICAO code.
        
        Returns:
            A list of dictionaries containing route details.
        """
        routes = self._load_routes()
        results = []
        
        for route in routes:
            if route['Arrival'] == arr_icao:
                results.append({
                    "flight_number": route['Flight Number'],
                    "departure": route['Departure'],
                    "arrival": route['Arrival'],
                    "aircraft": route['Aircraft'],
                    "flight_time": route['Flight Time'],
                    "airline": route['Airline']
                })
        
        return results

    async def get_all_airlines(self) -> List[str]:
        """
        Gets all unique airline names from the OWD routes CSV.
        
        Returns:
            A list of unique airline names.
        """
        routes = self._load_routes()
        airlines = set()
        
        for route in routes:
            if route['Airline'] and route['Airline'].strip():
                airlines.add(route['Airline'])
        
        return sorted(list(airlines))

    async def search_routes_by_aircraft(self, aircraft: str) -> List[Dict]:
        """
        Finds all routes that use a specific aircraft type.
        
        Args:
            aircraft: The aircraft type to search for (supports partial matching).
        
        Returns:
            A list of dictionaries containing route details.
        """
        routes = self._load_routes()
        results = []
        
        for route in routes:
            if aircraft.upper() in route['Aircraft'].upper():
                results.append({
                    "flight_number": route['Flight Number'],
                    "departure": route['Departure'],
                    "arrival": route['Arrival'],
                    "aircraft": route['Aircraft'],
                    "flight_time": route['Flight Time'],
                    "airline": route['Airline']
                })
        
        return results

    async def get_route_count(self) -> int:
        """
        Gets the total number of routes in the OWD CSV.
        
        Returns:
            The total count of routes.
        """
        routes = self._load_routes()
        return len(routes)