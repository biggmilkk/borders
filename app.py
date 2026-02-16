import streamlit as st
import geopandas as gpd
import requests
import pycountry
from shapely.geometry import shape
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw

st.set_page_config(page_title="Border Cutter", layout="wide")

# --- Styles ---
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #007bff; color: white; }
    </style>
    """, unsafe_allow_html=True)

# --- Logic ---
@st.cache_data(show_spinner=False)
def get_border(country_name):
    iso = pycountry.countries.get(name=country_name).alpha_3
    url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso}/ADM0/"
    r = requests.get(url, timeout=10).json()
    return gpd.read_file(r['gjDownloadURL'])

# --- Session State ---
if 'processed_data' not in st.session_state:
    st.session_state.processed_data = None

# --- UI ---
st.title("✂️ Minimal Border Cutter")

col_map, col_ctrl = st.columns([3, 1])

with col_ctrl:
    countries = sorted([c.name for c in pycountry.countries])
    target = st.selectbox("1. Target Country", countries, index=countries.index("Switzerland"))
    border_gdf = get_border(target)
    
    st.info("2. Draw on the map to select an area. It will automatically be cut to the official border.")
    
    # Download section (only visible if data exists)
    if st.session_state.processed_data is not None:
        st.success("Area Processed!")
        st.download_button(
            "💾 Download GeoJSON",
            data=st.session_state.processed_data,
            file_name=f"{target}_clipped.geojson",
            mime="application/json"
        )
        if st.button("Clear Canvas"):
            st.session_state.processed_data = None
            st.rerun()

with col_map:
    # Build Map
    m = folium.Map(location=[46.8, 8.2], zoom_start=8, tiles='CartoDB Positron')
    bounds = border_gdf.total_bounds
    m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
    
    # Static Country Layer
    folium.GeoJson(
        border_gdf, 
        style_function=lambda x: {'color': '#333', 'fillOpacity': 0.05, 'weight': 1.5}
    ).add_to(m)
    
    # Add Draw Tools
    Draw(export=False, position='topleft', 
         draw_options={'polyline':False, 'circle':False, 'marker':False, 'circlemarker':False}
    ).add_to(m)
    
    # Capture map data - using a key prevents it from resetting on every interaction
    map_output = st_folium(m, width="100%", height=600, key="cutter_map")

# --- Intersection Engine ---
# This runs only when a new drawing is detected
if map_output['last_active_drawing']:
    user_shape = shape(map_output['last_active_drawing']['geometry'])
    user_gdf = gpd.GeoDataFrame(geometry=[user_shape], crs="EPSG:4326")
    
    # Calculate intersection automatically
    clipped = gpd.overlay(user_gdf, border_gdf, how='intersection')
    if not clipped.empty:
        st.session_state.processed_data = clipped.to_json()
