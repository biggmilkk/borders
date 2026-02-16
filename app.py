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
st.set_page_config(page_title="Mapbox-Ready Border Snapper", layout="centered")

if 'result_gdf' not in st.session_state:
    st.session_state.result_gdf = None

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
st.title("🗺️ Mapbox-Optimized Snapper")
st.markdown("This version adds **anchor points** (densification) to prevent Mapbox from simplifying your borders.")

with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload Polygon", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("2. Target Country", options=countries)
    
    # Mapbox-specific optimization toggle
    densify = st.checkbox("Optimize for Mapbox (Add anchor points)", value=True)
    
    if st.button("🚀 Process & Snap", use_container_width=True):
        if uploaded_file:
            try:
                iso_code = get_iso3(selected_country)
                user_gdf = load_data(uploaded_file).to_crs(epsg=4326)
                user_geom = user_gdf.unary_union

                # Fetch geoBoundaries
                api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                r = requests.get(api_url).json()
                border_gdf = gpd.read_file(r['gjDownloadURL'])
                border_geom = border_gdf.unary_union

                # Snap & Merge Logic
                snapped_segment = user_geom.buffer(0.005).intersection(border_geom)
                final_union = unary_union([user_geom, snapped_segment])
                
                if isinstance(final_union, MultiPolygon):
                    final_poly = max(final_union.geoms, key=lambda a: a.area)
                else:
                    final_poly = final_union

                # --- MAPBOX OPTIMIZATION: DENSIFICATION ---
                if densify:
                    # segmentize adds points every ~100m (0.001 degrees) 
                    # so Mapbox can't "straighten" the curves.
                    final_poly = final_poly.segmentize(max_segment_length=0.001)

                st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                st.success("Processing Complete!")
            except Exception as e:
                st.error(f"Error: {e}")

# --- Results ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    poly = res.geometry.iloc[0]

    st.divider()
    m = folium.Map(location=[poly.centroid.y, poly.centroid.x], zoom_start=5)
    folium.GeoJson(res, style_function=lambda x: {'color': 'blue', 'weight': 2}).add_to(m)
    st_folium(m, width=700, height=400, key="map")

    c1, c2 = st.columns(2)
    
    # Export with high precision (15 decimals)
    geojson_out = res.to_json(na='null', show_bbox=False, drop_id=True)
    c1.download_button("💾 Download GeoJSON (Recommended for Mapbox)", geojson_out, "mapbox_ready.geojson")
    
    res.to_file("temp_out.kml", driver='KML')
    with open("temp_out.kml", "rb") as f:
        c2.download_button("💾 Download KML", f, "mapbox_ready.kml")
