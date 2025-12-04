import re
import os
from datetime import datetime
from typing import Optional, Union
from models.flight_details import FlightDetails

class PDFService:
    def __init__(self):
        pass
    
    def generate_flight_pdf(self, flight_data: Union[FlightDetails, dict], flight_type: str, pilot_user) -> Optional[bytes]:
        """Generate professional PDF flight document"""
        try:
            from fpdf import FPDF
            
            pdf = FPDF()
            pdf.add_page()
            
            # Add logos
            logo_paths = {"amiri": "assets/Amiri  flight logo.png", "executive": "assets/Qatar_Executive_Logo.png"}
            qatari_virtual_logo = "assets/Qatar_Virtual_logo.PNG"
            
            logo_path = logo_paths.get(flight_type)
            if logo_path and os.path.exists(logo_path):
                try:
                    pdf.image(logo_path, x=10, y=8, w=25)
                except Exception:
                    pass
            
            if os.path.exists(qatari_virtual_logo):
                try:
                    pdf.image(qatari_virtual_logo, x=155, y=8, w=45)
                except Exception:
                    pass
            
            pdf.ln(25)
            
            # Header
            pdf.set_font('Arial', 'B', 18)
            pdf.cell(0, 15, f'QATARI VIRTUAL {"AMIRI" if flight_type == "amiri" else "EXECUTIVE"}', 0, 1, 'C')
            pdf.set_font('Arial', 'B', 14)
            pdf.cell(0, 10, 'OPERATIONAL DOCUMENT', 0, 1, 'C')
            pdf.ln(10)
            
            # Flight Information
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 8, 'FLIGHT INFORMATION', 0, 1)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            # Convert to dict if dataclass
            if isinstance(flight_data, FlightDetails):
                data = flight_data.to_dict()
            else:
                data = flight_data
            
            pdf.set_font('Arial', '', 10)
            flight_info = [
                ('Flight Number:', self._clean_text(data['flight_number'])),
                ('Aircraft Type:', self._clean_text(data['aircraft_name'])),
                ('Route:', self._clean_text(data['route'])),
                ('Passengers:', f"{data['passengers']} PAX"),
                ('Cargo Weight:', f"{data['cargo']} KG"),
                ('Fuel Stop Required:', 'YES' if data['fuel_stop_required'] else 'NO'),
                ('Issue Date:', self._clean_text(data['current_date'])),
                ('Deadline:', self._clean_text(data['deadline']))
            ]
            
            for label, value in flight_info:
                pdf.cell(60, 6, label, 0, 0)
                pdf.cell(0, 6, str(value), 0, 1)
            
            # Fuel Stop Information
            if data.get('fuel_stop_required', False):
                pdf.ln(8)
                pdf.set_font('Arial', 'B', 12)
                pdf.cell(0, 8, 'FUEL STOP INFORMATION', 0, 1)
                pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                pdf.ln(5)
                pdf.set_font('Arial', '', 10)
                pdf.multi_cell(0, 5, "Fuel stop required. Plan fuel stops considering NOTAMs and weather conditions.")
            
            # Mission/Flight Briefing
            pdf.ln(8)
            pdf.set_font('Arial', 'B', 12)
            briefing_title = 'MISSION BRIEFING' if flight_type == 'amiri' else 'FLIGHT BRIEFING'
            pdf.cell(0, 8, briefing_title, 0, 1)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            if flight_type == "amiri":
                briefing_data = [
                    (f"Dossier: {self._clean_text(data.get('dignitary', 'N/A'))}", 
                     self._clean_text(data.get('dignitary_intro', 'No introduction available.'))),
                    ('Mission Objectives:', 
                     self._clean_text(data.get('mission_briefing', 'No briefing available.')))
                ]
            else:
                briefing_data = [
                    (f"Client Profile: {self._clean_text(data.get('client', 'N/A'))}", 
                     self._clean_text(data.get('client_intro', 'No client introduction available.'))),
                    ('Flight Purpose:', 
                     self._clean_text(data.get('mission_briefing', 'No briefing available.')))
                ]
            
            for title, content in briefing_data:
                pdf.set_font('Arial', 'B', 10)
                pdf.cell(0, 6, title, 0, 1)
                pdf.set_font('Arial', '', 10)
                pdf.multi_cell(0, 5, content)
                pdf.ln(3)
            
            # Manifest Details Section
            if data.get('manifest_details'):
                pdf.set_font('Arial', 'B', 10)
                pdf.cell(0, 6, 'Manifest Details:', 0, 1)
                pdf.set_font('Arial', '', 10)
                pdf.multi_cell(0, 5, self._clean_text(data.get('manifest_details', '')))
                pdf.ln(3)

            # Crew Assignment
            pdf.ln(8)
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 8, 'CREW ASSIGNMENT', 0, 1)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            pdf.set_font('Arial', '', 10)
            crew_info = [
                ('Pilot in Command:', self._clean_text(pilot_user.display_name)),
                ('Claim Time (UTC):', datetime.now().strftime('%d %B %Y at %H:%M UTC'))
            ]
            
            for label, value in crew_info:
                pdf.cell(60, 6, label, 0, 0)
                pdf.cell(0, 6, str(value), 0, 1)
            
            # Footer
            pdf.ln(15)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            pdf.set_font('Arial', 'I', 11)
            pdf.set_text_color(0, 102, 153)
            pdf.cell(0, 6, 'Generated by Qatari Virtual - Flight Operations Department', 0, 1, 'C')
            
            pdf.ln(3)
            pdf.set_fill_color(240, 240, 240)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font('Arial', 'B', 9)
            
            box_width = 180
            box_x = (210 - box_width) / 2
            pdf.set_x(box_x)
            pdf.cell(box_width, 8, 'HYPOTHETICAL FLIGHT - FOR PERSONAL FLIGHT SIMULATOR USE ONLY', 1, 1, 'C', True)
            
            pdf.set_font('Arial', '', 8)
            pdf.set_text_color(80, 80, 80)
            pdf.cell(0, 4, 'This document is not affiliated with any real Qatar Executive or government operations', 0, 1, 'C')
            pdf.set_text_color(0, 0, 0)
            
            return pdf.output()
        except (ImportError, Exception):
            return None
    
    def _clean_text(self, text) -> str:
        """Clean text for PDF generation"""
        return re.sub(r'[^\x00-\x7F]+', '', str(text))