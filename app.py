import streamlit as st
import geopandas as gpd
import requests
import pycountry
from shapely.geometry import shape
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw

st.set_page_config(page_title="Border Cutter", layout="wide")

@st.cache_data(show_spinner=False)
def get_border(country_name):
    iso = pycountry.countries.get(name=country_name).alpha_3
    url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso}/ADM0/"
    r = requests.get(url, timeout=10).json()
    return gpd.read_file(r['gjDownloadURL'])

# --- UI ---
st.title("✂️ Minimal Border Cutter")

col_map, col_ctrl = st.columns([3, 1])

with col_ctrl:
    countries = sorted([c.name for c in pycountry.countries])
    target = st.selectbox("1. Target Country", countries, index=countries.index("Switzerland"))
    border_gdf = get_border(target)
    
    st.markdown("### 2. Instructions")
    st.write("- Use the **Square** or **Polygon** tool.")
    st.write("- **Important:** If using Polygon, click the **start point** to finish.")
    
    # Placeholder for the download button
    download_placeholder = st.empty()

with col_map:
    m = folium.Map(location=[46.8, 8.2], zoom_start=8, tiles='CartoDB Positron')
    m.fit_bounds([[border_gdf.total_bounds[1], border_gdf.total_bounds[0]], 
                  [border_gdf.total_bounds[3], border_gdf.total_bounds[2]]])
    
    folium.GeoJson(border_gdf, style_function=lambda x: {'color': '#333', 'fillOpacity': 0.05, 'weight': 1.5}).add_to(m)
    
    Draw(export=False, position='topleft', 
         draw_options={'polyline':False, 'circle':False, 'marker':False, 'circlemarker':False, 'polygon':True}
    ).add_to(m)
    
    # We use all_drawings to catch any shape currently on the map
    map_output = st_folium(m, width="100%", height=600, key="cutter_map")

# --- Logic: Process any drawing found on map ---
if map_output and map_output.get('all_drawings'):
    # Take the most recent drawing
    last_draw = map_output['all_drawings'][-1]
    user_shape = shape(last_draw['geometry'])
    
    if user_shape.is_valid:
        user_gdf = gpd.GeoDataFrame(geometry=[user_shape], crs="EPSG:4326")
        clipped = gpd.overlay(user_gdf, border_gdf, how='intersection')
        
        if not clipped.empty:
            with col_ctrl:
                st.success("Target area locked!")
                download_placeholder.download_button(
                    "💾 Download Result",
                    data=clipped.to_json(),
                    file_name=f"{target}_clipped.geojson",
                    mime="application/json",
                    use_container_width=True
                )
