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

# Setup
fiona.supported_drivers['KML'] = 'rw'
st.set_page_config(page_title="Global Border Snapper", layout="centered")

# --- Helper: Dynamic Country List ---
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

# --- UI ---
st.title("🗺️ Global Border Snapper")
st.markdown("Snap your custom polygons to official international borders and merge them into one.")

# Inputs grouped for simplicity
with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload Polygon (GeoJSON, KML, KMZ)", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("2. Select Country to Snap To", options=countries)
    iso_code = get_iso3(selected_country)

if uploaded_file:
    if st.button("🚀 Process & Snap to Border", use_container_width=True):
        try:
            with st.status("Processing geospatial data...") as status:
                # 1. Load User Data
                user_gdf = load_data(uploaded_file)
                user_gdf = user_gdf.to_crs(epsg=4326)
                user_geom = user_gdf.unary_union

                # 2. Fetch geoBoundaries
                api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                r = requests.get(api_url).json()
                border_gdf = gpd.read_file(r['gjDownloadURL'])
                border_geom = border_gdf.unary_union

                # 3. Snap Logic: Buffer, Intersect, and Union
                # We use a 0.005 degree buffer (~500m) to find "close" edges
                snapped_segment = user_geom.buffer(0.005).intersection(border_geom)
                final_union = unary_union([user_geom, snapped_segment])
                
                # Single Contiguous Requirement: Take largest part
                if isinstance(final_union, MultiPolygon):
                    final_poly = max(final_union.geoms, key=lambda a: a.area)
                else:
                    final_poly = final_union

                result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                status.update(label="Complete!", state="complete")

            # --- Results Display ---
            st.subheader("Preview & Export")
            
            # Static Map (Keyed to avoid refresh loops)
            m = folium.Map(location=[final_poly.centroid.y, final_poly.centroid.x], zoom_start=5)
            folium.TileLayer('OpenStreetMap').add_to(m)
            folium.GeoJson(result_gdf, name="Result", style_function=lambda x: {'color': 'blue', 'weight': 4}).add_to(m)
            st_folium(m, width=700, height=400, key="output_map")

            # Simple Downloads
            c1, c2 = st.columns(2)
            c1.download_button("💾 Download GeoJSON", result_gdf.to_json(), f"{iso_code}_snapped.geojson")
            
            result_gdf.to_file("temp_out.kml", driver='KML')
            with open("temp_out.kml", "rb") as f:
                c2.download_button("💾 Download KML", f, f"{iso_code}_snapped.kml")
            os.remove("temp_out.kml")

        except Exception as e:
            st.error(f"Error: {e}. Check if the file contains valid polygon data.")
