import streamlit as st
import geopandas as gpd
import requests
import pycountry
import tempfile
import os
import fiona
from shapely.geometry import shape
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw

# --- Configuration ---
st.set_page_config(page_title="Geospatial International Border Mapper", layout="centered")

if 'KML' not in fiona.supported_drivers:
    fiona.supported_drivers['KML'] = 'rw'

# Professional UI styling
st.markdown("""
    <style>
    .main { background-color: #ffffff; }
    div.stButton > button {
        width: 100%;
        border-radius: 2px;
        height: 3.5em;
        background-color: #1a1a1a;
        color: white;
        border: none;
        margin-top: 10px;
    }
    /* Reduces the 'gray out' opacity during loading */
    .stApp [data-testid="stStatusWidget"] { display: none; }
    .block-container { max-width: 900px; padding-top: 2rem; }
    </style>
    """, unsafe_allow_html=True)

@st.cache_data(show_spinner=False)
def fetch_boundary(country_name):
    if not country_name: return None
    try:
        iso = pycountry.countries.get(name=country_name).alpha_3
        url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso}/ADM0/"
        r = requests.get(url, timeout=10).json()
        return gpd.read_file(r['gjDownloadURL'])
    except Exception: return None

# --- Header ---
st.title("Geospatial International Border Mapper")
st.caption("Standardized clipping of user-defined geometries against official ADM0 international boundaries.")

country_list = sorted([c.name for c in pycountry.countries])
selected_target = st.selectbox(
    "Select International Jurisdiction", 
    country_list, 
    index=None, 
    placeholder="Choose a country to load borders..."
)

boundary_gdf = fetch_boundary(selected_target)

st.markdown("---")
st.subheader("Define Area of Interest")

# --- Map Logic ---
# We center the map only once when the country is selected
if boundary_gdf is not None:
    b = boundary_gdf.total_bounds
    map_center = [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2]
    m = folium.Map(location=map_center, zoom_start=6, tiles='CartoDB Positron')
    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
    
    folium.GeoJson(
        boundary_gdf, 
        style_function=lambda x: {'color': '#1a1a1a', 'fillOpacity': 0.02, 'weight': 0.8},
        interactive=False
    ).add_to(m)
else:
    m = folium.Map(location=[20, 0], zoom_start=2, tiles='CartoDB Positron')

# Add existing result to map if present
if "active_result" in st.session_state and st.session_state.active_result is not None:
    folium.GeoJson(
        st.session_state.active_result,
        style_function=lambda x: {'color': '#0047AB', 'fillColor': '#0047AB', 'fillOpacity': 0.3, 'weight': 2}
    ).add_to(m)

Draw(
    export=False,
    position='topleft',
    draw_options={'polyline': False, 'circle': False, 'marker': False, 'circlemarker': False, 'polygon': True, 'rectangle': True}
).add_to(m)

# Execute map and capture data
map_interaction = st_folium(m, width="100%", height=550, key="workbench_map")

# --- Logic Processing ---
if map_interaction and map_interaction.get('all_drawings') and boundary_gdf is not None:
    latest_drawing = map_interaction['all_drawings'][-1]
    raw_shape = shape(latest_drawing['geometry'])
    
    if raw_shape.is_valid:
        input_gdf = gpd.GeoDataFrame(geometry=[raw_shape], crs="EPSG:4326")
        processed_intersection = gpd.overlay(input_gdf, boundary_gdf, how='intersection')
        
        if not processed_intersection.empty:
            final_gdf = processed_intersection[['geometry']].copy()
            # Check for changes to prevent redundant re-renders
            if "active_result" not in st.session_state or st.session_state.active_result is None or not final_gdf.equals(st.session_state.active_result):
                st.session_state.active_result = final_gdf
                st.rerun()

# --- Export Section (Isolated for Stability) ---
@st.fragment
def export_section():
    if "active_result" in st.session_state and st.session_state.active_result is not None:
        st.markdown("---")
        st.subheader("Export Results")
        
        export_gdf = st.session_state.active_result.copy()
        clean_name = selected_target.lower().replace(" ", "_") if selected_target else "country"
        final_filename = f"{clean_name}_border"
        
        col_json, col_kml, col_clear = st.columns(3)
        
        with col_json:
            st.download_button(
                label="Download GeoJSON",
                data=export_gdf.to_json(),
                file_name=f"{final_filename}.geojson",
                mime="application/json"
            )
        
        with col_kml:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.kml') as tmp:
                    export_gdf.to_file(tmp.name, driver='KML')
                    with open(tmp.name, "rb") as f:
                        st.download_button(
                            label="Download KML",
                            data=f.read(),
                            file_name=f"{final_filename}.kml",
                            mime="application/vnd.google-earth.kml+xml"
                        )
                os.remove(tmp.name)
            except:
                st.error("KML Unavailable")
                
        with col_clear:
            if st.button("Reset Canvas"):
                st.session_state.active_result = None
                st.rerun()

export_section()
