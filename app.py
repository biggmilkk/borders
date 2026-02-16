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
st.set_page_config(page_title="Geospatial Border Tool", layout="wide")

# Enable KML drivers in fiona
if 'KML' not in fiona.supported_drivers:
    fiona.supported_drivers['KML'] = 'rw'

st.markdown("""
    <style>
    .main { background-color: #ffffff; }
    div.stButton > button {
        width: 100%;
        border-radius: 2px;
        height: 3em;
        background-color: #1a1a1a;
        color: white;
        border: none;
    }
    div.stButton > button:hover {
        background-color: #333333;
        color: white;
    }
    </style>
    """, unsafe_allow_html=True)

@st.cache_data(show_spinner=False)
def fetch_boundary(country_name):
    if not country_name:
        return None
    try:
        iso = pycountry.countries.get(name=country_name).alpha_3
        url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso}/ADM0/"
        r = requests.get(url, timeout=10).json()
        return gpd.read_file(r['gjDownloadURL'])
    except Exception:
        return None

# --- Persistence ---
if 'active_result' not in st.session_state:
    st.session_state.active_result = None

# --- Header ---
st.title("Geospatial Border Sculptor")
st.caption("Precision clipping of user-defined geometries against national administrative boundaries.")

col_map, col_sidebar = st.columns([3, 1])

with col_sidebar:
    st.subheader("Parameters")
    country_list = sorted([c.name for c in pycountry.countries])
    selected_target = st.selectbox(
        "Country Selection", 
        country_list, 
        index=None, 
        placeholder="Select a jurisdiction..."
    )
    
    boundary_gdf = fetch_boundary(selected_target)
    
    st.markdown("---")
    st.subheader("Export Options")
    
    if st.session_state.active_result is not None:
        st.info("Intersection processed.")
        
        # GeoJSON Export
        st.download_button(
            label="Download GeoJSON",
            data=st.session_state.active_result.to_json(),
            file_name=f"{selected_target}_sculpted.geojson",
            mime="application/json",
            use_container_width=True
        )
        
        # KML Export Logic
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.kml') as tmp:
                # KML only supports certain geometry types; simplify or ensure 2D if needed
                st.session_state.active_result.to_file(tmp.name, driver='KML')
                with open(tmp.name, "rb") as f:
                    kml_data = f.read()
                
                st.download_button(
                    label="Download KML",
                    data=kml_data,
                    file_name=f"{selected_target}_sculpted.kml",
                    mime="application/vnd.google-earth.kml+xml",
                    use_container_width=True
                )
            os.remove(tmp.name)
        except Exception as e:
            st.error("KML conversion is currently unavailable for this geometry.")
        
        if st.button("Clear Canvas"):
            st.session_state.active_result = None
            st.rerun()
    else:
        st.write("Awaiting selection and input.")

with col_map:
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

    if st.session_state.active_result is not None:
        folium.GeoJson(
            st.session_state.active_result,
            style_function=lambda x: {
                'color': '#0047AB', 
                'fillColor': '#0047AB', 
                'fillOpacity': 0.3, 
                'weight': 2
            }
        ).add_to(m)

    Draw(
        export=False,
        position='topleft',
        draw_options={
            'polyline': False, 'circle': False, 'marker': False, 
            'circlemarker': False, 'polygon': True, 'rectangle': True
        }
    ).add_to(m)

    map_interaction = st_folium(m, width="100%", height=650, key="workbench_map")

# --- Processing Logic ---
if map_interaction and map_interaction.get('all_drawings') and boundary_gdf is not None:
    latest_drawing = map_interaction['all_drawings'][-1]
    raw_shape = shape(latest_drawing['geometry'])
    
    if raw_shape.is_valid:
        input_gdf = gpd.GeoDataFrame(geometry=[raw_shape], crs="EPSG:4326")
        processed_intersection = gpd.overlay(input_gdf, boundary_gdf, how='intersection')
        
        if not processed_intersection.empty:
            if st.session_state.active_result is None or not processed_intersection.equals(st.session_state.active_result):
                st.session_state.active_result = processed_intersection
                st.rerun()
