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
st.set_page_config(page_title="High-Fidelity Snapper", layout="wide")

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
st.title("🏔️ High-Fidelity Mapbox Snapper")
st.markdown("Use this version for jagged, complex borders like Switzerland/Italy to prevent Mapbox simplification.")

with st.sidebar:
    st.header("Upload & Settings")
    uploaded_file = st.file_uploader("Upload Polygon", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("Target Country", options=countries, index=countries.index("Italy") if "Italy" in countries else 0)
    
    st.subheader("Mapbox Protection")
    # Decreasing max_segment_length adds MORE points. 
    # 0.0005 degrees is roughly every 50 meters.
    point_density = st.slider("Anchor Point Spacing (Degrees)", 0.0001, 0.005, 0.0005, format="%.4f")
    st.caption("Lower value = More points = Less simplification in Mapbox.")

    if st.button("🚀 Process & Snap", use_container_width=True):
        if uploaded_file:
            iso_code = get_iso3(selected_country)
            user_gdf = load_data(uploaded_file).to_crs(epsg=4326)
            
            # Fetch geoBoundaries
            api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
            r = requests.get(api_url).json()
            border_gdf = gpd.read_file(r['gjDownloadURL'])
            
            # Snap Logic
            snapped = user_gdf.unary_union.buffer(0.005).intersection(border_gdf.unary_union)
            final_union = unary_union([user_gdf.unary_union, snapped])
            
            # Filter for largest contiguous polygon
            if isinstance(final_union, MultiPolygon):
                final_poly = max(final_union.geoms, key=lambda a: a.area)
            else:
                final_poly = final_union

            # HIGH-FIDELITY DENSIFICATION
            # This is the "secret sauce" to force Mapbox to keep the detail.
            final_poly = final_poly.segmentize(max_segment_length=point_density)

            st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")

# --- Display ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    poly = res.geometry.iloc[0]

    col1, col2 = st.columns([3, 1])

    with col1:
        # Map with Satellite Toggle to verify against terrain
        m = folium.Map(location=[poly.centroid.y, poly.centroid.x], zoom_start=12)
        folium.TileLayer(
            tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
            attr='Google',
            name='Google Satellite',
            overlay=False,
            control=True
        ).add_to(m)
        folium.TileLayer('OpenStreetMap').add_to(m)
        
        folium.GeoJson(res, name="Snapped Result", 
                       style_function=lambda x: {'color': '#FF3333', 'weight': 3, 'fillOpacity': 0.2}).add_to(m)
        folium.LayerControl().add_to(m)
        st_folium(m, width="100%", height=600, key="hf_map")

    with col2:
        st.subheader("Export")
        # 15-decimal precision is key for Mapbox high-zoom accuracy
        geojson_data = res.to_json(na='null', show_bbox=False, drop_id=True)
        st.download_button("📩 High-Precision GeoJSON", geojson_data, "mapbox_high_res.geojson", use_container_width=True)
        
        st.info("💡 **Mapbox Tip:** When uploading to Mapbox Studio, set the 'Simplification' slider to 0 in the Tileset settings.")
