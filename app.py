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

# Enable KML and KMZ drivers
fiona.supported_drivers['KML'] = 'rw'
fiona.supported_drivers['LIBKML'] = 'rw'

st.set_page_config(page_title="Border Snapper", layout="wide")

st.title("🌍 International Border Snapper & Merger")
st.markdown("""
Upload a polygon (GeoJSON, KML, or KMZ), and this app will:
1. **Snap** it to the official geoBoundaries international border.
2. **Merge** all parts into a single contiguous polygon.
""")

# --- Sidebar Inputs ---
st.sidebar.header("Settings")
uploaded_file = st.sidebar.file_uploader("Upload Polygon", type=['geojson', 'kml', 'kmz'])
iso_code = st.sidebar.text_input("Country ISO3 (e.g., USA, FRA, MEX)", value="USA").upper()
snap_buffer = st.sidebar.slider("Snapping Tolerance (Degrees)", 0.0001, 0.05, 0.005, step=0.0001)

def load_data(file):
    fname = file.name.lower()
    if fname.endswith('.kmz'):
        with ZipFile(file, 'r') as kmz:
            kml_name = [f for f in kmz.namelist() if f.endswith('.kml')][0]
            with kmz.open(kml_name, 'r') as kml_file:
                return gpd.read_file(kml_file, driver='KML')
    else:
        return gpd.read_file(file)

# --- Main Logic ---
if uploaded_file and iso_code:
    try:
        # 1. Load User Polygon
        user_gdf = load_data(uploaded_file)
        if user_gdf.crs is None:
            user_gdf.set_crs(epsg=4326, inplace=True)
        user_gdf = user_gdf.to_crs(epsg=4326)

        # 2. Fetch geoBoundaries Reference
        with st.spinner(f"Downloading {iso_code} borders..."):
            api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
            r = requests.get(api_url).json()
            border_gdf = gpd.read_file(r['gjDownloadURL'])

        # 3. Process: Snap, Intersect, and Merge
        with st.spinner("Snapping and merging geometries..."):
            # Ensure everything is a single geometry for processing
            user_geom = user_gdf.unary_union
            border_geom = border_gdf.unary_union
            
            # Snap: Intersection with a slight buffer to ensure overlap
            snapped = user_geom.buffer(snap_buffer).intersection(border_geom)
            
            # Combine original with snapped result and dissolve
            combined = unary_union([user_geom, snapped])
            
            # Requirement: One Contiguous Polygon
            # If the result is a MultiPolygon, we take the largest area to ensure contiguity
            if isinstance(combined, MultiPolygon):
                final_geom = max(combined.geoms, key=lambda a: a.area)
            else:
                final_geom = combined

            result_gdf = gpd.GeoDataFrame(geometry=[final_geom], crs="EPSG:4326")

        # --- Display Results ---
        col1, col2 = st.columns([2, 1])

        with col1:
            st.subheader("Map Preview")
            # Create Folium Map
            m = folium.Map(location=[final_geom.centroid.y, final_geom.centroid.x], zoom_start=6)
            
            # Style for the layers
            folium.GeoJson(border_gdf, name="International Border", 
                           style_function=lambda x: {'color': 'red', 'fillOpacity': 0}).add_to(m)
            folium.GeoJson(result_gdf, name="Snapped Result", 
                           style_function=lambda x: {'color': 'blue', 'fillColor': 'blue'}).add_to(m)
            
            folium.LayerControl().add_to(m)
            st_folium(m, width=700, height=500)

        with col2:
            st.subheader("Export Data")
            # GeoJSON Download
            geojson_str = result_gdf.to_json()
            st.download_button("💾 Download GeoJSON", geojson_str, "snapped.geojson", "application/json")

            # KML Download
            tmp_kml = "temp_output.kml"
            result_gdf.to_file(tmp_kml, driver='KML')
            with open(tmp_kml, "rb") as f:
                st.download_button("💾 Download KML", f, "snapped.kml", "application/vnd.google-earth.kml+xml")
            os.remove(tmp_kml)

    except Exception as e:
        st.error(f"Error processing files: {e}")
else:
    st.info("Please upload a file and enter an ISO code in the sidebar to begin.")
