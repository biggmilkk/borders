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

# Initialize session state for persistence
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
            kml_name = [f for f in kmz.namelist() if f.endswith('.kml')][0]
            with kmz.open(kml_name, 'r') as kml_file:
                return gpd.read_file(kml_file, driver='KML')
    return gpd.read_file(file)

# --- Main UI ---
st.title("Global Border Snapper")
st.markdown("Snap polygons to international borders. Optimized for high-detail Mapbox uploads.")

with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload Polygon (GeoJSON, KML, KMZ)", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("2. Target Country", options=countries)
    
    # High-fidelity setting: 0.0005 degrees is roughly every 50 meters.
    point_density = 0.0005 

    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.status("Snapping to international border...") as status:
                    iso_code = get_iso3(selected_country)
                    
                    # 1. Load User Data
                    user_gdf = load_data(uploaded_file).to_crs(epsg=4326)
                    user_geom = user_gdf.unary_union

                    # 2. Fetch geoBoundaries
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    r = requests.get(api_url).json()
                    border_gdf = gpd.read_file(r['gjDownloadURL'])
                    border_geom = border_gdf.unary_union

                    # 3. Snap and Merge Logic
                    snapped_segment = user_geom.buffer(0.005).intersection(border_geom)
                    final_union = unary_union([user_geom, snapped_segment])
                    
                    # Single Contiguous Requirement
                    if isinstance(final_union, MultiPolygon):
                        final_poly = max(final_union.geoms, key=lambda a: a.area)
                    else:
                        final_poly = final_union

                    # 4. Mapbox Optimization (Densification)
                    final_poly = final_poly.segmentize(max_segment_length=point_density)

                    st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                    status.update(label="Processing Complete", state="complete")
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            st.warning("Please upload a file first.")

# --- Persistent Results Area ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    poly = res.geometry.iloc[0]

    st.divider()
    st.subheader("Preview and Export")
    
    # Map Preview: Defaulting to OpenStreetMap (Non-Satellite)
    m = folium.Map(location=[poly.centroid.y, poly.centroid.x], zoom_start=12)
    
    # Adding OpenStreetMap as default
    folium.TileLayer('OpenStreetMap', name='Standard View').add_to(m)
    
    # Adding Satellite as an optional background
    folium.TileLayer(
        tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
        attr='Google',
        name='Satellite View',
        overlay=False,
        control=True
    ).add_to(m)
    
    folium.GeoJson(res, style_function=lambda x: {'color': '#0000FF', 'weight': 2, 'fillOpacity': 0.2}).add_to(m)
    folium.LayerControl(position='topright').add_to(m)
    
    st_folium(m, width=700, height=500, key="persistent_map")

    # Downloads
    c1, c2 = st.columns(2)
    
    # High-precision GeoJSON for Mapbox
    geojson_out = res.to_json(na='null', show_bbox=False, drop_id=True)
    c1.download_button("Download GeoJSON", geojson_out, "mapbox_ready.geojson", use_container_width=True)
    
    # KML Export
    res.to_file("temp_out.kml", driver='KML')
    with open("temp_out.kml", "rb") as f:
        c2.download_button("Download KML", f, "output.kml", use_container_width=True)
    os.remove("temp_out.kml")
