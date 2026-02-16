import streamlit as st
import geopandas as gpd
import requests
import fiona
import os
from zipfile import ZipFile
from shapely.ops import unary_union
from shapely.geometry import MultiPolygon
from streamlit_folium import st_folium
import folium

# Setup
fiona.supported_drivers['KML'] = 'rw'
st.set_page_config(page_title="Easy Border Snap", layout="centered")

# --- Helper: ISO Mapping ---
# A small sample; in a production app, you'd use the 'pycountry' library
ISO_MAP = {
    "United States": "USA", "Canada": "CAN", "Mexico": "MEX", 
    "France": "FRA", "Germany": "DEU", "United Kingdom": "GBR",
    "Brazil": "BRA", "Australia": "AUS", "India": "IND", "China": "CHN"
}

def load_data(file):
    fname = file.name.lower()
    if fname.endswith('.kmz'):
        with ZipFile(file, 'r') as kmz:
            kml_name = [f for f in kmz.namelist() if f.endswith('.kml')][0]
            with kmz.open(kml_name, 'r') as kml_file:
                return gpd.read_file(kml_file, driver='KML')
    return gpd.read_file(file)

# --- UI ---
st.title("📍 Simple Border Snapper")
st.info("Upload your file, pick a country, and download the cleaned result.")

col_a, col_b = st.columns(2)
with col_a:
    uploaded_file = st.file_uploader("1. Upload Polygon (GeoJSON, KML, KMZ)", type=['geojson', 'kml', 'kmz'])
with col_b:
    country_name = st.selectbox("2. Target Country", options=list(ISO_MAP.keys()))
    iso_code = ISO_MAP[country_name]

if uploaded_file:
    if st.button("🚀 Process & Snap Polygon"):
        try:
            # 1. Load & Standardize
            user_gdf = load_data(uploaded_file)
            user_gdf = user_gdf.to_crs(epsg=4326)
            user_geom = user_gdf.unary_union

            # 2. Fetch Border
            api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
            border_url = requests.get(api_url).json()['gjDownloadURL']
            border_gdf = gpd.read_file(border_url)
            border_geom = border_gdf.unary_union

            # 3. Snap & Clean Logic
            # Intersection creates the snapped edge; Union merges it with the original
            snapped = user_geom.buffer(0.005).intersection(border_geom)
            final_union = unary_union([user_geom, snapped])
            
            # Keep only the largest contiguous piece
            if isinstance(final_union, MultiPolygon):
                final_poly = max(final_union.geoms, key=lambda a: a.area)
            else:
                final_poly = final_union

            result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")

            # --- Results ---
            st.success("Done! View and download below.")
            
            # Map Preview (Static key prevents refresh loops)
            m = folium.Map(location=[final_poly.centroid.y, final_poly.centroid.x], zoom_start=5)
            folium.GeoJson(result_gdf, style_function=lambda x: {'color': 'blue', 'weight': 3}).add_to(m)
            st_folium(m, width=700, height=400, key="static_map")

            # Downloads
            dl1, dl2 = st.columns(2)
            dl1.download_button("📩 Download GeoJSON", result_gdf.to_json(), "snapped.geojson")
            
            result_gdf.to_file("out.kml", driver='KML')
            with open("out.kml", "rb") as f:
                dl2.download_button("📩 Download KML", f, "snapped.kml")

        except Exception as e:
            st.error(f"Something went wrong: {e}")
