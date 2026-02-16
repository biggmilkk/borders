import streamlit as st
import geopandas as gpd
import requests
from shapely.ops import unary_union
from shapely.geometry import MultiPolygon
import fiona

# Enable KML support
fiona.supported_drivers['KML'] = 'rw'

st.title("🌍 International Border Snapper")
st.markdown("Upload a polygon, pick a country, and I'll snap the edges to the geoBoundaries border.")

# 1. Inputs
uploaded_file = st.file_uploader("Upload your Polygon (GeoJSON or KML)", type=['geojson', 'kml'])
iso_code = st.text_input("Enter Country ISO Code (e.g., USA, MEX, CAN)", value="USA").upper()
snap_dist = st.slider("Snapping Buffer (Degrees)", 0.0001, 0.05, 0.005, format="%.4f")

if uploaded_file and iso_code:
    try:
        # Load User Data
        user_gdf = gpd.read_file(uploaded_file)
        if user_gdf.crs is None:
            user_gdf.set_crs(epsg=4326, inplace=True)
        user_gdf = user_gdf.to_crs(epsg=4326)

        # 2. Fetch geoBoundaries
        with st.spinner(f"Fetching {iso_code} borders..."):
            gb_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
            gb_data = requests.get(gb_url).json()
            border_gdf = gpd.read_file(gb_data['gjDownloadURL'])

        # 3. Process: Snap & Merge
        # Buffer slightly to ensure "touching" then intersect
        buffered_user = user_gdf.geometry.buffer(snap_dist)
        snapped = unary_union(buffered_user).intersection(border_gdf.unary_union)
        
        # Merge with original to keep internal shape but use border edge
        final_union = unary_union([user_gdf.unary_union, snapped])
        
        # Collapse into one contiguous polygon
        if isinstance(final_union, MultiPolygon):
            # Take the largest contiguous piece
            final_poly = max(final_union.geoms, key=lambda a: a.area)
        else:
            final_poly = final_union

        output_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")

        # 4. Success and Downloads
        st.success("Polygon snapped and merged!")
        
        col1, col2 = st.columns(2)
        
        # GeoJSON Export
        geojson_data = output_gdf.to_json()
        col1.download_button("Download GeoJSON", geojson_data, "snapped_output.geojson", "application/json")
        
        # KML Export (Temporary file handling)
        output_gdf.to_file("temp.kml", driver='KML')
        with open("temp.kml", "rb") as f:
            col2.download_button("Download KML", f, "snapped_output.kml", "application/vnd.google-earth.kml+xml")

    except Exception as e:
        st.error(f"Error: {e}")
