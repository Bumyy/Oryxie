import json
from fpdf import FPDF
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import DictionaryObject, NumberObject, NameObject, ArrayObject, TextStringObject
import os
import io

class PDF(FPDF):
    def __init__(self):
        super().__init__('P', 'mm', 'A4')
        self.checkbox_positions = []
    
    def header(self):
        self.set_font('Arial', 'B', 16)
        self.cell(0, 10, 'Flight Checklist', 0, 1, 'C')
        self.set_font('Arial', '', 10)
        self.cell(0, 5, self.title, 0, 1, 'C')
        
        # Add logo in top right corner
        try:
            logo_path = os.path.join('assets', 'Qatar_Virtual_logo.PNG')
            if os.path.exists(logo_path):
                self.image(logo_path, x=170, y=8, w=25)
        except:
            pass  # Continue without logo if there's an error
        
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

    def section_title(self, title):
        self.set_font('Arial', 'B', 12)
        self.cell(0, 8, title, 0, 1, 'L')
        self.line(self.get_x(), self.get_y(), self.get_x() + 190, self.get_y())
        self.ln(2)
        
    def checklist_item(self, text, value="", is_dynamic=False, indent=0):
        self.set_font('Arial', '', 11)
        
        start_x = self.get_x() + indent
        self.set_x(start_x)
        
        dot_length = 35 - len(text) - (indent // 2)
        full_text = f"{text} {' . ' * dot_length}"
        self.cell(100, 7, full_text)
        
        if is_dynamic:
            self.set_font('Arial', 'B', 11)
        self.cell(80, 7, value, 0, 0, 'R')
        self.set_font('Arial', '', 11)
        
        # Store checkbox position
        checkbox_x = self.get_x() + 2
        checkbox_y = self.get_y() + 1
        self.checkbox_positions.append((self.page_no(), checkbox_x, checkbox_y))
        
        # Draw placeholder box
        self.rect(checkbox_x, checkbox_y, 5, 5)
        
        self.ln(7)

class ChecklistPDFService:
    def __init__(self):
        self.template_path = os.path.join('assets', 'checklist_template.json')
        self.aircraft_db_path = os.path.join('assets', 'aircrafts.json')
        with open(self.template_path, 'r') as f:
            self.template = json.load(f)
        with open(self.aircraft_db_path, 'r') as f:
            self.aircraft_db = json.load(f)

    def _get_performance_data(self, aircraft_data, load):
        perf = {}
        takeoff_perf = next((p for p in aircraft_data['takeoff_data'] if p['load_range'][0] <= load <= p['load_range'][1]), None)
        if takeoff_perf:
            flaps = takeoff_perf['flaps'].split('/')[0]
            perf['takeoff_flaps'] = flaps
            perf['n1_target'] = takeoff_perf['n1']
            perf['vr_speed'] = str(takeoff_perf['vr'])
            perf['va_speed'] = str(takeoff_perf['va'])
        return perf

    def _add_checkboxes(self, input_file, output_file, positions):
        reader = PdfReader(input_file)
        writer = PdfWriter()
        
        for page in reader.pages:
            writer.add_page(page)
        
        for idx, (page_num, x_mm, y_mm) in enumerate(positions):
            # Convert mm to points (72 points per inch, 25.4mm per inch)
            x_pt = x_mm * 2.83465
            y_pt = (297 - y_mm - 5) * 2.83465  # Flip Y, A4 = 297mm
            
            writer.add_annotation(
                page_number=page_num - 1,
                annotation=DictionaryObject({
                    NameObject("/Type"): NameObject("/Annot"),
                    NameObject("/Subtype"): NameObject("/Widget"),
                    NameObject("/Rect"): ArrayObject([
                        NumberObject(x_pt), NumberObject(y_pt),
                        NumberObject(x_pt + 14.17), NumberObject(y_pt + 14.17)
                    ]),
                    NameObject("/FT"): NameObject("/Btn"),
                    NameObject("/T"): TextStringObject(f"cb_{page_num}_{idx}"),
                    NameObject("/V"): NameObject("/Off"),
                    NameObject("/AS"): NameObject("/Off"),
                    NameObject("/Ff"): NumberObject(0),
                })
            )
        
        with open(output_file, 'wb') as f:
            writer.write(f)

    def generate_checklist_pdf(self, aircraft_type: str, load: int, checklist_type: str, direction: str) -> io.BytesIO:
        if aircraft_type not in self.aircraft_db:
            raise ValueError(f"Aircraft type '{aircraft_type}' not found in the database.")
            
        aircraft_info = self.aircraft_db[aircraft_type]
        ac_data = aircraft_info['performance_data']
        
        placeholders = {'load_percentage': str(load)}
        perf = self._get_performance_data(ac_data, load)
        placeholders.update(perf)
        
        cruise_perf = next((p for p in ac_data['cruise_profile'][direction.lower()] if p['load_range'][0] <= load <= p['load_range'][1]), None)
        placeholders['cruise_altitude'] = cruise_perf['altitude'] if cruise_perf else "N/A"
        
        placeholders.update({
            'initial_climb_vs': ac_data['climb_vs_profile']['0_5000'],
            'vs_5k': ac_data['climb_vs_profile']['5000_15000'],
            'vs_15k': ac_data['climb_vs_profile']['15000_24000'],
            'vs_24k': ac_data['climb_vs_profile']['24000_cruise'],
            'initial_speed': ac_data['speed_profile']['initial_speed'],
            'accel_speed_10k': ac_data['speed_profile']['above_10k'],
            'mach_transition_alt': ac_data['speed_profile']['mach_transition_alt'],
            'mach_speed': ac_data['speed_profile']['mach']
        })

        pdf = PDF()
        pdf.set_title(f"{aircraft_info['properties']['full_name']} | Load: {load}% | {direction.title()}bound")
        pdf.add_page()
        
        # Add header section with NOTAMS
        if 'header' in self.template:
            header = self.template['header']
            pdf.section_title(header['title'])
            pdf.set_font('Arial', '', 10)
            for line in header['content']:
                pdf.cell(0, 5, line, 0, 1, 'L')
            pdf.ln(5)
        
        for section in self.template['checklist']:
            pdf.section_title(section['title'])
            
            # Handle text sections
            if 'text_section' in section:
                pdf.set_font('Arial', '', 10)
                for line in section['text_section']:
                    # Split long lines to prevent text cutoff
                    words = line.split(' ')
                    current_line = ""
                    for word in words:
                        test_line = current_line + (" " if current_line else "") + word
                        if pdf.get_string_width(test_line) < 180:  # Leave margin
                            current_line = test_line
                        else:
                            if current_line:
                                pdf.cell(0, 5, current_line, 0, 1, 'L')
                            current_line = word
                    if current_line:
                        pdf.cell(0, 5, current_line, 0, 1, 'L')
                pdf.ln(1)
            
            # Handle first special section immediately after text
            if 'special_section' in section:
                if section['special_section'] == 'descent_speed_profile':
                    descent_profile = ac_data.get('descent_speed_profile', {})
                    pdf.set_font('Arial', '', 10)
                    for phase, speed in descent_profile.items():
                        phase_text = phase.replace('_', ' ').replace('fl', 'FL').upper()
                        pdf.cell(0, 5, f"  {phase_text}: {speed}", 0, 1, 'L')
                    pdf.ln(2)
            
            if 'items' in section:
                for item in section.get('items', []):
                    value = item['value']
                    for key, val in placeholders.items():
                        value = value.replace(f"{{{key}}}", str(val))
                    pdf.checklist_item(item['text'], value, item.get('is_dynamic', False))
            
            if 'special_section' in section:
                if section['special_section'] == 'engine_start':
                    for eng_num in aircraft_info.get('engine_start_sequence', []):
                        pdf.checklist_item(f"ENGINE {eng_num}", "START")
                        pdf.checklist_item(f"ENGINE {eng_num}", "STABLE (20%)", indent=10)

                elif section['special_section'] == 'flap_retraction_below_10k':
                    aircraft_code = aircraft_type
                    
                    # Use hardcoded schedule for A321 and A350, dynamic for B77W
                    if aircraft_code in ['A321', 'A350']:
                        flap_schedule = aircraft_info.get('flap_retraction_schedule', [])
                        for flap_step in flap_schedule:
                            if flap_step['speed'] <= 250:
                                pdf.checklist_item(f"AS SPEED INCREASES, AT {flap_step['speed']} KTS", f"SET FLAPS {flap_step['setting']}", is_dynamic=True)
                    
                    elif aircraft_code == 'B77W':
                        # Dynamic logic for B77W only
                        takeoff_flap = perf.get('takeoff_flaps', '5')
                        
                        if takeoff_flap == '5':
                            pdf.checklist_item("AS SPEED INCREASES, AT 245 KTS", "SET FLAPS 1", is_dynamic=True)
                        elif takeoff_flap == '15':
                            pdf.checklist_item("AS SPEED INCREASES, AT 230 KTS", "SET FLAPS 5", is_dynamic=True)
                            pdf.checklist_item("AS SPEED INCREASES, AT 245 KTS", "SET FLAPS 1", is_dynamic=True)
                
                elif section['special_section'] == 'flap_retraction_above_10k':
                    aircraft_code = aircraft_type
                    
                    # Use hardcoded schedule for A321 and A350, dynamic for B77W
                    if aircraft_code in ['A321', 'A350']:
                        flap_schedule = aircraft_info.get('flap_retraction_schedule', [])
                        for flap_step in flap_schedule:
                            if flap_step['speed'] > 250:
                                pdf.checklist_item(f"AS SPEED INCREASES, AT {flap_step['speed']} KTS", f"SET FLAPS {flap_step['setting']}", is_dynamic=True)
                    
                    elif aircraft_code == 'B77W':
                        # Dynamic logic for B77W only - always show "SET FLAPS 0" at 265 KTS above 10k
                        pdf.checklist_item("AS SPEED INCREASES, AT 265 KTS", "SET FLAPS 0", is_dynamic=True)
                
                elif section['special_section'] == 'step_climb':
                     step_climbs = [p for p in ac_data['cruise_profile'][direction.lower()] if p['load_range'][1] < load]
                     if step_climbs:
                         pdf.checklist_item("STEP CLIMB (IF POSSIBLE)", "")
                         for step in sorted(step_climbs, key=lambda x: x['load_range'][1], reverse=True):
                             pdf.checklist_item(f"AT {step['load_range'][1]}% LOAD", f"CLIMB TO {step['altitude']}", is_dynamic=True, indent=10)
                
                elif section['special_section'] == 'flap_deploy_above_10k':
                    flap_speeds = ac_data['flap_speeds']
                    # Sort flap settings by speed (descending order for deployment)
                    sorted_flaps = sorted(flap_speeds.items(), key=lambda x: x[1], reverse=True)
                    for flap_setting, speed in sorted_flaps:
                        if speed > 250:
                            pdf.checklist_item(f"AS SPEED DECREASES, AT {speed} KTS", f"SET FLAPS {flap_setting}", is_dynamic=True)
                
                elif section['special_section'] == 'flap_deploy_below_10k':
                    flap_speeds = ac_data['flap_speeds']
                    # Sort flap settings by speed (descending order for deployment)
                    sorted_flaps = sorted(flap_speeds.items(), key=lambda x: x[1], reverse=True)
                    for flap_setting, speed in sorted_flaps:
                        if speed <= 250:
                            pdf.checklist_item(f"AS SPEED DECREASES, AT {speed} KTS", f"SET FLAPS {flap_setting}", is_dynamic=True)
                
                # Skip descent_speed_profile here since it's handled after text_section
            
            # Handle items_after_special (for climb items after above 10k flap retraction)
            if 'items_after_special' in section:
                for item in section['items_after_special']:
                    value = item['value']
                    for key, val in placeholders.items():
                        value = value.replace(f"{{{key}}}", str(val))
                    pdf.checklist_item(item['text'], value, item.get('is_dynamic', False))
            
            # Handle second special section
            if 'special_section_2' in section:
                if section['special_section_2'] == 'flap_deploy_above_10k':
                    flap_speeds = ac_data['flap_speeds']
                    sorted_flaps = sorted(flap_speeds.items(), key=lambda x: x[1], reverse=True)
                    for flap_setting, speed in sorted_flaps:
                        if speed > 250:
                            pdf.checklist_item(f"AS SPEED DECREASES, AT {speed} KTS", f"SET FLAPS {flap_setting}", is_dynamic=True)
                
                elif section['special_section_2'] == 'landing_data_table':
                    # Get landing data for loads at or below takeoff load
                    landing_data = ac_data.get('landing_data', [])
                    filtered_data = [ld for ld in landing_data if ld['load_range'][1] <= load]
                    if filtered_data:
                        # Sort by load range in descending order
                        filtered_data = sorted(filtered_data, key=lambda x: x['load_range'][1], reverse=True)
                        
                        pdf.ln(2)
                        pdf.set_font('Arial', 'B', 9)
                        pdf.cell(0, 4, 'LANDING DATA (Possible Load Ranges)', 0, 1, 'L')
                        pdf.set_font('Arial', 'B', 8)
                        # Table headers - smaller size to fit on one page
                        pdf.cell(35, 5, 'Load Range', 1, 0, 'C')
                        pdf.cell(20, 5, 'Flaps', 1, 0, 'C')
                        pdf.cell(20, 5, 'Vapp', 1, 0, 'C')
                        pdf.cell(20, 5, 'Vflare', 1, 1, 'C')
                        pdf.set_font('Arial', '', 8)
                        # Table data - smaller font and row height
                        for ld in filtered_data:
                            pdf.cell(35, 5, f"{ld['load_range'][0]}-{ld['load_range'][1]}%", 1, 0, 'C')
                            pdf.cell(20, 5, ld['flaps'], 1, 0, 'C')
                            pdf.cell(20, 5, str(ld['vapp']), 1, 0, 'C')
                            pdf.cell(20, 5, str(ld['vflare']), 1, 1, 'C')
                        pdf.ln(2)
            
            # Handle items_after_table (for items after landing data table)
            if 'items_after_table' in section:
                for item in section['items_after_table']:
                    value = item['value']
                    for key, val in placeholders.items():
                        value = value.replace(f"{{{key}}}", str(val))
                    pdf.checklist_item(item['text'], value, item.get('is_dynamic', False))

            pdf.ln(2)

        temp_file = f"temp_{aircraft_type}_{checklist_type}.pdf"
        final_file = f"checklist_{aircraft_type}_{checklist_type}.pdf"
        
        pdf.output(temp_file)
        
        try:
            self._add_checkboxes(temp_file, final_file, pdf.checkbox_positions)
            os.remove(temp_file)
        except:
            if os.path.exists(temp_file):
                if os.path.exists(final_file):
                    os.remove(final_file)
                os.rename(temp_file, final_file)
        
        # Read into memory and cleanup
        with open(final_file, 'rb') as f:
            pdf_buffer = io.BytesIO(f.read())
            
        pdf_buffer.name = final_file # Set filename for discord.File
        pdf_buffer.seek(0)
        
        if os.path.exists(final_file):
            os.remove(final_file)
            
        return pdf_buffer
