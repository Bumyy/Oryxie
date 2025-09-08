from dataclasses import dataclass
from typing import Optional

@dataclass
class FlightDetails:
    flight_number: str
    aircraft_name: str
    passengers: int
    cargo: int
    route: str
    fuel_stop_required: bool
    current_date: str
    deadline: str
    
    # Optional fields for different flight types
    dignitary: Optional[str] = None
    mission_type: Optional[str] = None
    client: Optional[str] = None
    purpose: Optional[str] = None
    
    # Optional AI-generated fields
    dignitary_intro: Optional[str] = None
    mission_briefing: Optional[str] = None
    deadline_rationale: Optional[str] = None
    client_intro: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for backward compatibility"""
        return {k: v for k, v in self.__dict__.items() if v is not None}