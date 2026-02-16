import streamlit as st
import geopandas as gpd
import requests
import pycountry
from shapely.geometry import shape, Polygon
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw

st.set_page_config(page_title="Border Cutter", layout="wide")

# --- Functions ---
def get_iso3(name):
    try:
        return pycountry.countries.get(name=name).alpha_3
    except:
        return None

@st.cache_data(show_spinner=False)
def get_country_border(iso_code):
    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
    r = requests.get(api_url, timeout=10).json()
    return gpd.read_file(r['gjDownloadURL'])

# --- Sidebar ---
st.sidebar.title("🎨 Border Cutter")
countries = sorted([c.name for c in pycountry.countries])
selected_country = st.sidebar.selectbox("1. Pick a Country", countries, index=countries.index("Switzerland"))

# --- State Management ---
if 'processed_gdf' not in st.session_state:
    st.session_state.processed_gdf = None

# --- Main App ---
iso = get_iso3(selected_country)
border_gdf = get_country_border(iso)

st.subheader(f"2. Draw the area you want to keep in {selected_country}")
st.info("Use the polygon or rectangle tool on the left of the map to select the southern region.")

# Initialize Map
m = folium.Map()
# Fit to country
bounds = border_gdf.total_bounds
m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

# Show the country border as a reference
folium.GeoJson(
    border_gdf, 
    style_function=lambda x: {'color': '#666', 'fillOpacity': 0.1, 'weight': 1},
    interactive=False
).add_to(m)

# Add Drawing Tools
draw = Draw(
    export=False,
    draw_options={
        'polyline': False, 'circle': False, 'marker': False, 
        'circlemarker': False, 'polygon': True, 'rectangle': True
    }
)
draw.add_to(m)

# Display Map and capture output
output = st_folium(m, width=900, height=600, key="main_map")

# --- Processing Logic ---
if output['last_active_drawing']:
    # Convert user drawing to a Shapely geometry
    user_draw_coords = output['last_active_drawing']['geometry']
    user_shape = shape(user_draw_coords)
    user_gdf = gpd.GeoDataFrame(geometry=[user_shape], crs="EPSG:4326")

    if st.button("✂️ Cut to Border", use_container_width=True):
        with st.spinner("Calculating perfect intersection..."):
            # The "Cookie Cutter" Magic
            # This keeps only the parts of the user's drawing that are INSIDE the country
            result = gpd.overlay(user_gdf, border_gdf, how='intersection')
            
            if not result.empty:
                st.session_state.processed_gdf = result
                st.success("Successfully cut to international border!")
            else:
                st.error("Your drawing doesn't overlap with the country!")

# --- Export Section ---
if st.session_state.processed_gdf is not None:
    st.divider()
    st.subheader("3. Download Results")
    final_res = st.session_state.processed_gdf
    
    c1, c2 = st.columns(2)
    c1.download_button(
        "Download GeoJSON", 
        final_res.to_json(), 
        f"{selected_country}_cutout.geojson",
        use_container_width=True
    )
    
    # Simple table to show area
    area_sq_km = final_res.to_crs(epsg=3857).area.sum() / 10**6
    st.metric("Total Area Captured", f"{area_sq_km:,.2f} km²")
