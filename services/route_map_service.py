import io
import asyncio
import airportsdata
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import pyproj
import numpy as np
import logging

class RouteMapService:
    def __init__(self):
        self.airports = airportsdata.load('ICAO')
        self.geod = pyproj.Geod(ellps='WGS84')
        
        # --- MAX PERFORMANCE CONFIGURATION ---
        # 1. Loading '110m' resolution (Lowest detail, fastest render)
        # 2. Pre-loading features in __init__ to save CPU per request
        self.land_low = cfeature.NaturalEarthFeature(
            'physical', 'land', '110m', facecolor='#202225')
            
        self.borders_low = cfeature.NaturalEarthFeature(
            'cultural', 'admin_0_boundary_lines_land', '110m',
            edgecolor='gray', linewidth=0.5, alpha=0.5, facecolor='none')
            
        self.coast_low = cfeature.NaturalEarthFeature(
            'physical', 'coastline', '110m',
            edgecolor='#dcdcdc', linewidth=0.7, facecolor='none')

    def _generate_image_sync(self, dep_icao: str, arr_icao: str, duration: int = 0):
        # 1. Setup
        dep_icao = dep_icao.upper()
        arr_icao = arr_icao.upper()
        
        if dep_icao not in self.airports or arr_icao not in self.airports:
            raise ValueError(f"Invalid Airport Code: {dep_icao} or {arr_icao}")

        dep_data = self.airports[dep_icao]
        arr_data = self.airports[arr_icao]
        
        # --- OPTIMIZATION: Reduce Points ---
        # 40 points is enough for a smooth curve at low resolution
        n_points = 40
        result = self.geod.inv_intermediate(
            dep_data['lon'], dep_data['lat'], 
            arr_data['lon'], arr_data['lat'], 
            npts=n_points,
            return_back_azimuth=False
        )
        
        # Handle different return formats
        if hasattr(result, 'lons') and hasattr(result, 'lats'):
            route_lons = result.lons
            route_lats = result.lats
        elif isinstance(result, tuple) and len(result) == 2:
            route_lons, route_lats = result
        else:
            raise ValueError(f"Unexpected result format from inv_intermediate: {type(result)}")
        
        # Combine points & Unwrap (Dateline Fix)
        lons = np.array([dep_data['lon']] + list(route_lons) + [arr_data['lon']])
        lats = np.array([dep_data['lat']] + list(route_lats) + [arr_data['lat']])

        lons_rad = np.radians(lons)
        lons_unwrapped = np.degrees(np.unwrap(lons_rad))

        # Calculate Bounding Box
        min_lon, max_lon = np.min(lons_unwrapped), np.max(lons_unwrapped)
        min_lat, max_lat = np.min(lats), np.max(lats)

        # Padding & Aspect Ratio
        width = max_lon - min_lon
        height = max_lat - min_lat
        
        pad_x = np.clip(width * 0.15, 0.5, 15.0)
        pad_y = np.clip(height * 0.15, 0.5, 15.0)

        min_lon -= pad_x
        max_lon += pad_x
        min_lat -= pad_y
        max_lat += pad_y

        # Recalculate dimensions for Ratio enforcement
        width = max_lon - min_lon
        height = max_lat - min_lat
        target_aspect = 2.0
        current_aspect = width / height

        if current_aspect > target_aspect:
            # Too Wide -> Increase Height
            target_height = width / target_aspect
            diff = target_height - height
            min_lat -= diff / 2
            max_lat += diff / 2
        else:
            # Too Tall -> Increase Width
            target_width = height * target_aspect
            diff = target_width - width
            min_lon -= diff / 2
            max_lon += diff / 2

        # --- OPTIMIZATION: Low DPI ---
        # dpi=72 is standard screen resolution. Very low memory usage.
        fig = Figure(figsize=(10, 5), dpi=72, facecolor='#2f3136')
        canvas = FigureCanvasAgg(fig)

        # Map Projection
        mid_lon_raw = (min_lon + max_lon) / 2
        mid_lon_clamped = (mid_lon_raw + 180) % 360 - 180
        projection = ccrs.Mercator(central_longitude=mid_lon_clamped)
        
        ax = fig.add_subplot(1, 1, 1, projection=projection)
        ax.set_extent([min_lon, max_lon, min_lat, max_lat], crs=ccrs.PlateCarree())

        # Drawing
        ax.set_facecolor='#2f3136' # Ocean Hack (No polygon drawing)
        ax.add_feature(self.land_low)
        ax.add_feature(self.borders_low)
        ax.add_feature(self.coast_low)

        # Plot Route
        ax.plot(lons_unwrapped, lats, color='#800020', linewidth=2.5, transform=ccrs.PlateCarree())

        # --- ARROW LOGIC (Fixed Size Icon) ---
        from matplotlib.markers import MarkerStyle

        # 1. Find the middle of the route
        mid_idx = len(lons_unwrapped) // 2
        mid_lon = lons_unwrapped[mid_idx]
        mid_lat = lats[mid_idx]

        # 2. Calculate the Angle (Direction)
        # We look at the next point in the list to see where the plane is going
        d_lon = lons_unwrapped[mid_idx + 1] - lons_unwrapped[mid_idx]
        d_lat = lats[mid_idx + 1] - lats[mid_idx]
        
        # Math to find the angle (in degrees)
        angle = np.degrees(np.arctan2(d_lat, d_lon))

        # 3. Create a Rotated Marker
        # We use the '>' shape (which points East by default)
        # and rotate it by our calculated flight angle.
        marker_style = MarkerStyle(marker='>', fillstyle='full')
        marker_style._transform = marker_style.get_transform().rotate_deg(angle)

        # 4. Plot the Arrow
        # markersize=14 : Fixes the size (it won't get huge on long flights)
        # color='#E0E0E0' : Qatar Airways Silver
        # zorder=10 : Ensures it sits ON TOP of the red line
        ax.plot(mid_lon, mid_lat, 
                marker=marker_style, 
                color='#E0E0E0',           # Silver fill
                markeredgecolor='black',   # Thin black outline for contrast
                markeredgewidth=1,
                markersize=20,             # Fixed size (pixels)
                transform=ccrs.PlateCarree(), 
                zorder=10)

        # Dots
        ax.plot(lons_unwrapped[0], lats[0], color='white', marker='o', markersize=6, transform=ccrs.PlateCarree())
        ax.plot(lons_unwrapped[-1], lats[-1], color='white', marker='o', markersize=6, transform=ccrs.PlateCarree())

        # Labels
        lat_span = max_lat - min_lat
        label_offset = lat_span * 0.04 if lat_span > 2 else lat_span * 0.08
        
        ax.text(lons_unwrapped[0], lats[0] - label_offset, dep_icao, color='white', 
                ha='center', va='top', transform=ccrs.PlateCarree(), fontweight='bold', fontsize=11)
        ax.text(lons_unwrapped[-1], lats[-1] - label_offset, arr_icao, color='white', 
                ha='center', va='top', transform=ccrs.PlateCarree(), fontweight='bold', fontsize=11)

        ax.axis('off')
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

        buf = io.BytesIO()
        canvas.print_png(buf)
        buf.seek(0)
        plt.close(fig) # Clear memory immediately
        
        return buf

    async def create_route_map(self, dep: str, arr: str, duration: int = 0):
        try:
            return await asyncio.to_thread(self._generate_image_sync, dep, arr, duration)
        except Exception as e:
            logging.error(f"Map generation failed for {dep}-{arr}: {e}")
            return "Map Generation Error"