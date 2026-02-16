import streamlit as st
import geopandas as gpd
import requests
import fiona
import os
import pycountry
from zipfile import ZipFile
from shapely.geometry import MultiPolygon
from streamlit_folium import st_folium
import folium

# Setup drivers
fiona.supported_drivers['KML'] = 'rw'
st.set_page_config(page_title="Global Border Snapper", layout="centered")

if 'result_gdf' not in st.session_state:
    st.session_state.result_gdf = None

def get_iso3(name):
    return pycountry.countries.get(name=name).alpha_3

def load_data(file):
    fname = file.name.lower()
    if fname.endswith('.kmz'):
        with ZipFile(file, 'r') as kmz:
            kml_names = [f for f in kmz.namelist() if f.endswith('.kml')]
            with kmz.open(kml_names[0], 'r') as kml_file:
                return gpd.read_file(kml_file, driver='KML')
    return gpd.read_file(file)

st.title("Global Border Snapper")

with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload Polygon", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("2. Target Country", options=countries)
    
    # Range increased: 0.05 is roughly 5.5km
    snap_distance = st.slider("Snap Sensitivity (Degrees)", 0.001, 0.05, 0.01, format="%.3f")

    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.status("Performing Selective Snap...") as status:
                    iso_code = get_iso3(selected_country)
                    
                    # 1. Load and Fix User Data
                    user_gdf = load_data(uploaded_file).to_crs(epsg=4326)
                    # Fix self-intersections and geometry issues
                    user_gdf['geometry'] = user_gdf.geometry.make_valid()
                    user_geom = user_gdf.geometry.union_all()

                    # 2. Fetch official border
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    r = requests.get(api_url).json()
                    border_gdf = gpd.read_file(r['gjDownloadURL'])
                    border_geom = border_gdf.geometry.union_all()

                    # 3. SELECTIVE SNAP LOGIC
                    # Buffer the boundary to create a 'catchment' area
                    search_zone = user_geom.boundary.buffer(snap_distance)
                    
                    # Get segments of the border that are close to the user geometry
                    relevant_border = border_geom.intersection(search_zone)
                    
                    # Ensure we have a valid geometry object to work with
                    if relevant_border is not None and not relevant_border.is_empty:
                        # Merge the original with the nearby border segments
                        final_union = user_geom.union(relevant_border)
                    else:
                        final_union = user_geom
                    
                    # Cleanup slivers and ensure solid geometry
                    # This replaces the failing .buffer call with a safe sequence
                    final_poly_geom = final_union.buffer(0.00001).buffer(-0.00001)

                    if isinstance(final_poly_geom, MultiPolygon):
                        final_poly = max(final_poly_geom.geoms, key=lambda a: a.area)
                    else:
                        final_poly = final_poly_geom

                    # 4. Densification for Mapbox
                    final_poly = final_poly.segmentize(max_segment_length=0.0005)

                    st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                    status.update(label="Processing Complete", state="complete")
            except Exception as e:
                st.error(f"Geospatial Error: {e}")
        else:
            st.warning("Please upload a file first.")

# --- Results Area ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    bounds = res.total_bounds
    
    if not any(map(lambda x: str(x) == 'nan', bounds)):
        map_bounds = [[bounds[1], bounds[0]], [bounds[3], bounds[2]]]
        st.divider()
        st.subheader("Preview and Export")
        
        m = folium.Map(tiles='OpenStreetMap')
        m.fit_bounds(map_bounds)
        folium.GeoJson(res, style_function=lambda x: {'color': '#0000FF', 'weight': 2, 'fillOpacity': 0.2}).add_to(m)
        st_folium(m, width=700, height=500, key="persistent_map")

        c1, c2 = st.columns(2)
        geojson_out = res.to_json()
        c1.download_button("Download GeoJSON", geojson_out, "snapped.geojson", use_container_width=True)
        
        res.to_file("temp.kml", driver='KML')
        with open("temp.kml", "rb") as f:
            c2.download_button("Download KML", f, "snapped.kml", use_container_width=True)
        os.remove("temp.kml")
