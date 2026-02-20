import re
import os
from datetime import datetime
from typing import Optional, Union
from models.flight_details import FlightDetails
from services.ai_service import AIService

class PDFService:
    def __init__(self):
        self.ai_service = AIService()
    
    def _get_model_code(self, data: dict = None):
        if data and 'ai_model' in data:
            model = data['ai_model']
            if 'gemini' in model.lower():
                return 'GMN'
            elif 'gpt' in model.lower():
                return 'GPT'
            elif 'claude' in model.lower():
                return 'CLD'
            elif 'llama' in model.lower():
                return 'LLM'
            return 'AI'
        return 'UNK'
    
    def generate_flight_pdf(self, flight_data: Union[FlightDetails, dict], flight_type: str, pilot_user, pilot_info: dict = None) -> Optional[bytes]:
        """Generate professional PDF flight document"""
        print(f"[PDF DEBUG] PDF generation started for {flight_type} flight")
        try:
            from fpdf import FPDF
            
            # Convert to dict if dataclass
            if isinstance(flight_data, FlightDetails):
                data = flight_data.to_dict()
            else:
                data = flight_data
            
            # DEBUG: Print all available data keys
            print(f"[PDF DEBUG] Available data keys: {list(data.keys())}")
            print(f"[PDF DEBUG] Dignitary intro: {data.get('dignitary_intro')}")
            print(f"[PDF DEBUG] Counterpart intro: {data.get('counterpart_intro')}")
            print(f"[PDF DEBUG] Mission purpose: {data.get('mission_purpose')}")
            print(f"[PDF DEBUG] Mission urgency: {data.get('mission_urgency')}")
            print(f"[PDF DEBUG] Manifest details: {data.get('manifest_details')}")
            
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
            pdf.cell(0, 10, f'{"MISSION BRIEF" if flight_type == "amiri" else "FLIGHT BRIEF"}', 0, 1, 'C')
            pdf.ln(10)
            
            # Flight Information
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 8, 'FLIGHT INFORMATION', 0, 1)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
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
                # Dossier
                if data.get('dignitary_intro'):
                    pdf.set_font('Arial', 'B', 10)
                    pdf.cell(0, 6, 'Dossier:', 0, 1)
                    pdf.set_font('Arial', '', 10)
                    dossier_text = self._clean_text(data.get('dignitary_intro')).replace('**', '').replace('*', '')
                    pdf.multi_cell(0, 5, dossier_text.strip())
                    pdf.ln(3)
                
                # Purpose
                if data.get('mission_briefing'):
                    pdf.set_font('Arial', 'B', 10)
                    pdf.cell(0, 6, 'Purpose:', 0, 1)
                    pdf.set_font('Arial', '', 10)
                    purpose_text = self._clean_text(data.get('mission_briefing')).replace('**', '').replace('*', '')
                    pdf.multi_cell(0, 5, purpose_text.strip())
                    pdf.ln(3)
                
                # Payload
                if data.get('manifest_details'):
                    pdf.set_font('Arial', 'B', 10)
                    pdf.cell(0, 6, 'Payload:', 0, 1)
                    pdf.set_font('Arial', '', 10)
                    payload_text = self._clean_text(data.get('manifest_details')).replace('**', '').replace('*', '')
                    pdf.multi_cell(0, 5, payload_text.strip())
                    
            else:
                # Executive flight sections
                if data.get('client_intro'):
                    pdf.set_font('Arial', 'B', 10)
                    pdf.cell(0, 6, 'Client:', 0, 1)
                    pdf.set_font('Arial', '', 10)
                    client_text = self._clean_text(data.get('client_intro')).replace('**', '').replace('*', '')
                    pdf.multi_cell(0, 5, client_text.strip())
                    pdf.ln(3)
                
                if data.get('mission_briefing'):
                    pdf.set_font('Arial', 'B', 10)
                    pdf.cell(0, 6, 'Purpose:', 0, 1)
                    pdf.set_font('Arial', '', 10)
                    purpose_text = self._clean_text(data.get('mission_briefing')).replace('**', '').replace('*', '')
                    pdf.multi_cell(0, 5, purpose_text.strip())
                    pdf.ln(3)
                
                if data.get('manifest_details'):
                    pdf.set_font('Arial', 'B', 10)
                    pdf.cell(0, 6, 'Manifest:', 0, 1)
                    pdf.set_font('Arial', '', 10)
                    manifest_text = self._clean_text(data.get('manifest_details')).replace('**', '').replace('*', '')
                    pdf.multi_cell(0, 5, manifest_text.strip())

            # Crew Assignment
            pdf.ln(8)
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 8, 'CREW ASSIGNMENT', 0, 1)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            # Get formatted pilot info
            if pilot_info and 'callsign' in pilot_info and 'rank' in pilot_info:
                # Extract clean name from display name
                import re
                name_match = re.sub(r'QRV\d{3,}', '', pilot_user.display_name).strip()
                pilot_name = name_match if name_match else pilot_user.display_name
                
                formatted_pilot = f"Senior Captain {pilot_name} | {pilot_info['callsign']} | {pilot_info['rank']} Award Holder"
            else:
                # Fallback
                import re
                name_match = re.sub(r'QRV\d{3,}', '', pilot_user.display_name).strip()
                pilot_name = name_match if name_match else pilot_user.display_name
                formatted_pilot = f"Senior Captain {pilot_name}"
            
            pdf.set_font('Arial', '', 10)
            crew_info = [
                ('Pilot in Command:', self._clean_text(formatted_pilot)),
                ('Claim Time (UTC):', datetime.now().strftime('%d %B %Y at %H:%M UTC'))
            ]
            
            for label, value in crew_info:
                pdf.cell(60, 6, label, 0, 0)
                pdf.cell(0, 6, str(value), 0, 1)
            
            # Footer with metadata
            pdf.ln(15)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            # Document metadata
            model_code = self._get_model_code(data)
            doc_id = f"QRV-{flight_type.upper()[:3]}-{model_code}-{datetime.now().strftime('%Y-%m')}-{data['flight_number'].replace('QRV', '')}"
            pdf.set_font('Arial', '', 8)
            pdf.set_text_color(80, 80, 80)
            pdf.cell(0, 4, f'Document ID: {doc_id} | Version: 1.5 BETA', 0, 1, 'C')
            pdf.cell(0, 4, f'Generated: {datetime.now().strftime("%d %B %Y at %H:%M UTC")} | Oryxie Bot', 0, 1, 'C')
            if data.get('ai_model'):
                pdf.cell(0, 4, f"AI Model: {data['ai_model']}", 0, 1, 'C')
            
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
        except (ImportError, Exception) as e:
            print(f"[PDF DEBUG] Exception in PDF generation: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _clean_text(self, text) -> str:
        """Clean text for PDF generation"""
        return re.sub(r'[^\x00-\x7F]+', '', str(text))
