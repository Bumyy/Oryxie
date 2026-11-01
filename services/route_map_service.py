import io
import asyncio
import logging

try:
    import airportsdata
except ImportError:
    airportsdata = None

try:
    import pyproj
except ImportError:
    pyproj = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.markers import MarkerStyle
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_MAP_LIBS = True
except ImportError:
    HAS_MAP_LIBS = False

log = logging.getLogger(__name__)


class RouteMapService:
    def __init__(self):
        self.airports = {}
        if airportsdata:
            try:
                self.airports = airportsdata.load('ICAO')
            except Exception as e:
                log.warning("Could not load airportsdata: %s", e)

        self.geod = None
        if pyproj:
            try:
                self.geod = pyproj.Geod(ellps='WGS84')
            except Exception as e:
                log.warning("Could not initialize pyproj Geod: %s", e)

        self.land_low = None
        self.borders_low = None
        self.coast_low = None

        if HAS_MAP_LIBS:
            try:
                self.land_low = cfeature.NaturalEarthFeature(
                    'physical', 'land', '110m', facecolor='#202225')
                self.borders_low = cfeature.NaturalEarthFeature(
                    'cultural', 'admin_0_boundary_lines_land', '110m',
                    edgecolor='gray', linewidth=0.5, alpha=0.5, facecolor='none')
                self.coast_low = cfeature.NaturalEarthFeature(
                    'physical', 'coastline', '110m',
                    edgecolor='#dcdcdc', linewidth=0.9, facecolor='none')
            except Exception as e:
                log.warning("Could not pre-load cartopy features: %s", e)

    def _generate_image_sync(
        self,
        dep_icao: str,
        arr_icao: str,
        duration: int = 0,
        flight_plan_waypoints: list = None,
        current_lat: float = None,
        current_lon: float = None,
        heading: float = None,
        registration: str = None,
    ):
        if not HAS_MAP_LIBS or not self.geod or np is None:
            log.debug("Route map rendering skipped: required geospatial libraries not available.")
            return None

        dep_icao = (dep_icao or "").strip().upper()
        arr_icao = (arr_icao or "").strip().upper()

        if dep_icao not in self.airports or arr_icao not in self.airports:
            log.warning("Invalid airport code for route map: %s or %s", dep_icao, arr_icao)
            return None

        dep_data = self.airports[dep_icao]
        arr_data = self.airports[arr_icao]

        # Extract waypoints from flight plan if provided
        route_pts = []
        if flight_plan_waypoints:
            for item in flight_plan_waypoints:
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    try:
                        route_pts.append((float(item[0]), float(item[1])))
                    except (TypeError, ValueError):
                        pass
                elif isinstance(item, dict):
                    loc = item.get("location") if isinstance(item.get("location"), dict) else item
                    lat = loc.get("latitude", loc.get("lat"))
                    lon = loc.get("longitude", loc.get("lon"))
                    if lat is not None and lon is not None:
                        try:
                            route_pts.append((float(lat), float(lon)))
                        except (TypeError, ValueError):
                            pass

        if len(route_pts) >= 2:
            # Connect departure airport if not adjacent
            if (abs(dep_data['lat'] - route_pts[0][0]) > 0.5 or 
                abs(dep_data['lon'] - route_pts[0][1]) > 0.5):
                route_pts.insert(0, (dep_data['lat'], dep_data['lon']))
            # Connect arrival airport if not adjacent
            if (abs(arr_data['lat'] - route_pts[-1][0]) > 0.5 or 
                abs(arr_data['lon'] - route_pts[-1][1]) > 0.5):
                route_pts.append((arr_data['lat'], arr_data['lon']))

            lats = np.array([p[0] for p in route_pts])
            lons = np.array([p[1] for p in route_pts])
        else:
            # Fallback to great-circle arc
            n_points = 60
            result = self.geod.inv_intermediate(
                dep_data['lon'], dep_data['lat'], 
                arr_data['lon'], arr_data['lat'], 
                npts=n_points,
                return_back_azimuth=False
            )
            if hasattr(result, 'lons') and hasattr(result, 'lats'):
                route_lons = result.lons
                route_lats = result.lats
            elif isinstance(result, tuple) and len(result) == 2:
                route_lons, route_lats = result
            else:
                raise ValueError(f"Unexpected result format from inv_intermediate: {type(result)}")

            lons = np.array([dep_data['lon']] + list(route_lons) + [arr_data['lon']])
            lats = np.array([dep_data['lat']] + list(route_lats) + [arr_data['lat']])

        lons_rad = np.radians(lons)
        lons_unwrapped = np.degrees(np.unwrap(lons_rad))

        # Handle current aircraft position if provided
        curr_lon_unwrapped = None
        if current_lat is not None and current_lon is not None:
            try:
                current_lat = float(current_lat)
                current_lon = float(current_lon)
                ref_lon = float(np.median(lons_unwrapped))
                k = round((ref_lon - current_lon) / 360.0)
                curr_lon_unwrapped = current_lon + k * 360.0
            except (ValueError, TypeError):
                current_lat = None
                curr_lon_unwrapped = None

        # Bounding box calculation
        all_lons = np.append(lons_unwrapped, curr_lon_unwrapped) if curr_lon_unwrapped is not None else lons_unwrapped
        all_lats = np.append(lats, current_lat) if current_lat is not None else lats
        min_lon, max_lon = float(np.min(all_lons)), float(np.max(all_lons))
        min_lat, max_lat = float(np.min(all_lats)), float(np.max(all_lats))

        # Padding & Aspect Ratio
        width = max_lon - min_lon
        height = max_lat - min_lat

        pad_x = float(np.clip(width * 0.15, 0.5, 15.0))
        pad_y = float(np.clip(height * 0.15, 0.5, 15.0))

        min_lon -= pad_x
        max_lon += pad_x
        min_lat -= pad_y
        max_lat += pad_y

        width = max_lon - min_lon
        height = max_lat - min_lat
        target_aspect = 2.0
        current_aspect = (width / height) if height > 0 else target_aspect

        if current_aspect > target_aspect:
            target_height = width / target_aspect
            diff = target_height - height
            min_lat -= diff / 2
            max_lat += diff / 2
        else:
            target_width = height * target_aspect
            diff = target_width - width
            min_lon -= diff / 2
            max_lon += diff / 2

        fig = Figure(figsize=(10, 5), dpi=90, facecolor='#2f3136')
        canvas = FigureCanvasAgg(fig)

        mid_lon_raw = (min_lon + max_lon) / 2
        mid_lon_clamped = (mid_lon_raw + 180) % 360 - 180
        projection = ccrs.Mercator(central_longitude=mid_lon_clamped)

        ax = fig.add_subplot(1, 1, 1, projection=projection)
        ax.set_extent([min_lon, max_lon, min_lat, max_lat], crs=ccrs.PlateCarree())

        ax.set_facecolor='#2f3136'
        if self.land_low:
            ax.add_feature(self.land_low)
        if self.borders_low:
            ax.add_feature(self.borders_low)
        if self.coast_low:
            ax.add_feature(self.coast_low)

        # Plot route line (Qatar maroon)
        ax.plot(lons_unwrapped, lats, color='#800020', linewidth=2.5, transform=ccrs.PlateCarree(), zorder=5)

        # Plot waypoint dots if flight plan was provided
        if len(route_pts) >= 2 and len(route_pts) <= 120:
            ax.plot(
                lons_unwrapped[1:-1], lats[1:-1],
                color='#D0D0D0',
                marker='.',
                markersize=4,
                linestyle='None',
                transform=ccrs.PlateCarree(),
                zorder=6,
            )

        lat_span = max_lat - min_lat
        label_offset = lat_span * 0.04 if lat_span > 2 else lat_span * 0.08

        # Plot Aircraft or Midpoint Arrow
        if current_lat is not None and curr_lon_unwrapped is not None:
            if heading is not None:
                # Math angle for '>' marker (default points East = 0 deg math):
                angle = 90.0 - float(heading)
            else:
                idx = int(np.argmin(np.hypot(lons_unwrapped - curr_lon_unwrapped, lats - current_lat)))
                if idx < len(lons_unwrapped) - 1:
                    d_lon = lons_unwrapped[idx + 1] - lons_unwrapped[idx]
                    d_lat = lats[idx + 1] - lats[idx]
                else:
                    d_lon = lons_unwrapped[-1] - lons_unwrapped[-2]
                    d_lat = lats[-1] - lats[-2]
                angle = float(np.degrees(np.arctan2(d_lat, d_lon)))

            marker_style = MarkerStyle(marker='>', fillstyle='full')
            marker_style._transform = marker_style.get_transform().rotate_deg(angle)

            # Glowing badge / circle under aircraft
            ax.plot(
                curr_lon_unwrapped, current_lat,
                marker='o',
                color='#800020',
                markersize=14,
                transform=ccrs.PlateCarree(),
                zorder=11,
            )
            # Plane / directional marker
            ax.plot(
                curr_lon_unwrapped, current_lat,
                marker=marker_style,
                color='#FFFFFF',
                markeredgecolor='black',
                markeredgewidth=1,
                markersize=18,
                transform=ccrs.PlateCarree(),
                zorder=12,
            )
            if registration:
                ax.text(
                    curr_lon_unwrapped, current_lat + label_offset * 0.8,
                    registration,
                    color='#FFFFFF',
                    fontweight='bold',
                    fontsize=9,
                    ha='center',
                    va='bottom',
                    transform=ccrs.PlateCarree(),
                    bbox=dict(boxstyle='round,pad=0.25', facecolor='#202225', edgecolor='#800020', alpha=0.9),
                    zorder=13,
                )
        else:
            # Static midpoint arrow (Flight Board mode)
            mid_idx = len(lons_unwrapped) // 2
            mid_lon = lons_unwrapped[mid_idx]
            mid_lat = lats[mid_idx]
            d_lon = lons_unwrapped[mid_idx + 1] - lons_unwrapped[mid_idx]
            d_lat = lats[mid_idx + 1] - lats[mid_idx]
            angle = float(np.degrees(np.arctan2(d_lat, d_lon)))
            marker_style = MarkerStyle(marker='>', fillstyle='full')
            marker_style._transform = marker_style.get_transform().rotate_deg(angle)
            ax.plot(
                mid_lon, mid_lat, 
                marker=marker_style, 
                color='#E0E0E0',
                markeredgecolor='black',
                markeredgewidth=1,
                markersize=20,
                transform=ccrs.PlateCarree(), 
                zorder=10,
            )

        # Origin / Destination Dots
        ax.plot(lons_unwrapped[0], lats[0], color='white', marker='o', markersize=6, transform=ccrs.PlateCarree(), zorder=10)
        ax.plot(lons_unwrapped[-1], lats[-1], color='white', marker='o', markersize=6, transform=ccrs.PlateCarree(), zorder=10)

        # Labels
        ax.text(lons_unwrapped[0], lats[0] - label_offset, dep_icao, color='white', 
                ha='center', va='top', transform=ccrs.PlateCarree(), fontweight='bold', fontsize=11, zorder=10)
        ax.text(lons_unwrapped[-1], lats[-1] - label_offset, arr_icao, color='white', 
                ha='center', va='top', transform=ccrs.PlateCarree(), fontweight='bold', fontsize=11, zorder=10)

        ax.axis('off')
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

        buf = io.BytesIO()
        canvas.print_png(buf)
        buf.seek(0)
        plt.close(fig)

        return buf

    async def create_route_map(
        self,
        dep: str,
        arr: str,
        duration: int = 0,
        flight_plan_waypoints: list = None,
        current_lat: float = None,
        current_lon: float = None,
        heading: float = None,
        registration: str = None,
    ):
        try:
            return await asyncio.to_thread(
                self._generate_image_sync,
                dep,
                arr,
                duration,
                flight_plan_waypoints,
                current_lat,
                current_lon,
                heading,
                registration,
            )
        except Exception as e:
            logging.error(f"Map generation failed for {dep}-{arr}: {e}")
            return None

    async def create_live_flight_map(
        self,
        dep: str,
        arr: str,
        flight_plan_waypoints: list = None,
        current_lat: float = None,
        current_lon: float = None,
        heading: float = None,
        registration: str = None,
    ):
        return await self.create_route_map(
            dep=dep,
            arr=arr,
            flight_plan_waypoints=flight_plan_waypoints,
            current_lat=current_lat,
            current_lon=current_lon,
            heading=heading,
            registration=registration,
        )
