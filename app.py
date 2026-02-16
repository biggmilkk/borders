import streamlit as st
import geopandas as gpd
import requests
import fiona
import os
import pycountry
from zipfile import ZipFile
from shapely.ops import unary_union
from shapely.geometry import MultiPolygon
from streamlit_folium import st_folium
import folium

# Setup drivers
fiona.supported_drivers['KML'] = 'rw'
st.set_page_config(page_title="Global Border Snapper", layout="centered")

if 'result_gdf' not in st.session_state:
    st.session_state.result_gdf = None

# --- Helpers ---
countries = sorted([c.name for c in pycountry.countries])

def get_iso3(name):
    return pycountry.countries.get(name=name).alpha_3

def load_data(file):
    fname = file.name.lower()
    if fname.endswith('.kmz'):
        with ZipFile(file, 'r') as kmz:
            kml_names = [f for f in kmz.namelist() if f.endswith('.kml')]
            with kmz.open(kml_names[0], 'r') as kml_file:
                return gpd.read_file(kml_file, driver='KML')
    return gpd.read_file(file)

# --- Main UI ---
st.title("Global Border Snapper")
st.markdown("Selective snapping: Magnetize edges near borders while keeping internal lines intact.")

with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload Polygon", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("2. Target Country", options=countries)
    
    # 0.005 degrees is approx 500m. This is the "magnet" reach.
    snap_distance = st.slider("Snap Sensitivity (Degrees)", 0.001, 0.02, 0.005, format="%.3f")

    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.status("Performing Selective Snap...") as status:
                    iso_code = get_iso3(selected_country)
                    
                    # 1. Load User Data
                    user_gdf = load_data(uploaded_file).to_crs(epsg=4326)
                    user_geom = user_gdf.unary_union

                    # 2. Fetch official border
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    r = requests.get(api_url).json()
                    border_gdf = gpd.read_file(r['gjDownloadURL'])
                    border_geom = border_gdf.unary_union

                    # 3. SELECTIVE SNAP LOGIC
                    # Create a "search zone" around your polygon edges
                    search_zone = user_geom.boundary.buffer(snap_distance)
                    
                    # Find only the parts of the international border that fall inside that search zone
                    relevant_border_segments = border_geom.intersection(search_zone)
                    
                    # Merge those specific border segments into your original polygon
                    # This "increases or decreases" the edge to meet the border
                    final_union = unary_union([user_geom, relevant_border_segments])
                    
                    # Cleanup: Ensure it remains a solid polygon (filling small slivers)
                    final_poly_geom = final_union.buffer(0.00001).buffer(-0.00001)

                    if isinstance(final_poly_geom, MultiPolygon):
                        final_poly = max(final_poly_geom.geoms, key=lambda a: a.area)
                    else:
                        final_poly = final_poly_geom

                    # 4. Densification for Mapbox
                    final_poly = final_poly.segmentize(max_segment_length=0.0005)

                    st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                    status.update(label="Processing Complete", state="complete")
            except Exception as e:
                st.error(f"Error: {e}")

# --- Results Area ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    bounds = res.total_bounds
    
    if not any(map(lambda x: str(x) == 'nan', bounds)):
        map_bounds = [[bounds[1], bounds[0]], [bounds[3], bounds[2]]]
        st.divider()
        st.subheader("Preview and Export")
        
        m = folium.Map(tiles='OpenStreetMap')
        m.fit_bounds(map_bounds)
        folium.GeoJson(res, style_function=lambda x: {'color': '#0000FF', 'weight': 2, 'fillOpacity': 0.2}).add_to(m)
        st_folium(m, width=700, height=500, key="persistent_map")

        c1, c2 = st.columns(2)
        geojson_out = res.to_json()
        c1.download_button("Download GeoJSON", geojson_out, "snapped.geojson", use_container_width=True)
        
        res.to_file("temp.kml", driver='KML')
        with open("temp.kml", "rb") as f:
            c2.download_button("Download KML", f, "snapped.kml", use_container_width=True)
        os.remove("temp.kml")
