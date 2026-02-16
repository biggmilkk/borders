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

# --- Session State Initialization ---
if 'result_gdf' not in st.session_state:
    st.session_state.result_gdf = None
if 'original_gdf' not in st.session_state:
    st.session_state.original_gdf = None

# --- Helpers ---
countries = sorted([c.name for c in pycountry.countries])

def get_iso3(name):
    return pycountry.countries.get(name=name).alpha_3

def load_data(file):
    fname = file.name.lower()
    # KML/KMZ often contain multiple layers. We need to read and combine them.
    if fname.endswith('.kmz'):
        with ZipFile(file, 'r') as kmz:
            kml_name = [f for f in kmz.namelist() if f.endswith('.kml')][0]
            with kmz.open(kml_name, 'r') as kml_file:
                layers = fiona.listlayers(kml_file)
                gdfs = [gpd.read_file(kml_file, layer=l, driver='KML') for l in layers]
                return gpd.pd.concat(gdfs, ignore_index=True)
    elif fname.endswith('.kml'):
        layers = fiona.listlayers(file)
        gdfs = [gpd.read_file(file, layer=l, driver='KML') for l in layers]
        return gpd.pd.concat(gdfs, ignore_index=True)
    
    return gpd.read_file(file)

# --- Main UI ---
st.title("Global Border Snapper")
st.markdown("Snap polygons to international borders. Optimized for high-detail Mapbox uploads.")

with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload Polygon (GeoJSON, KML, KMZ)", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("2. Target Country", options=countries)
    
    point_density = 0.0005 

    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.status("Processing geospatial data...") as status:
                    iso_code = get_iso3(selected_country)
                    
                    # 1. Load User Data and combine all layers
                    raw_gdf = load_data(uploaded_file)
                    if raw_gdf.crs is None:
                        raw_gdf.set_crs(epsg=4326, inplace=True)
                    user_gdf = raw_gdf.to_crs(epsg=4326)
                    
                    # Store original for comparison
                    st.session_state.original_gdf = user_gdf.copy()
                    
                    # Modern replacement for unary_union
                    user_geom = user_gdf.geometry.union_all()

                    # 2. Fetch geoBoundaries
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    r = requests.get(api_url).json()
                    border_gdf = gpd.read_file(r['gjDownloadURL'])
                    border_geom = border_gdf.geometry.union_all()

                    # 3. Snap and Merge Logic
                    snapped_segment = user_geom.buffer(0.005).intersection(border_geom)
                    final_union = unary_union([user_geom, snapped_segment]) if hasattr(user_geom, 'union') else user_geom.union(snapped_segment)
                    
                    # Handle contiguous requirement
                    if isinstance(final_union, MultiPolygon):
                        final_poly = max(final_union.geoms, key=lambda a: a.area)
                    else:
                        final_poly = final_union

                    # 4. Mapbox Optimization (Densification)
                    final_poly = final_poly.segmentize(max_segment_length=point_density)

                    st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                    status.update(label="Processing Complete", state="complete")
                    
                    st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            st.warning("Please upload a file first.")

# --- Results Area ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    orig = st.session_state.original_gdf
    
    st.divider()
    st.subheader("Preview and Export")
    st.caption("Red: Original | Blue: Snapped result")
    
    bounds = res.total_bounds
    map_bounds = [[bounds[1], bounds[0]], [bounds[3], bounds[2]]]

    m = folium.Map(tiles='OpenStreetMap')
    m.fit_bounds(map_bounds)
    
    folium.GeoJson(
        orig, 
        style_function=lambda x: {'color': '#FF0000', 'weight': 2, 'fillOpacity': 0, 'dashArray': '5, 5'}
    ).add_to(m)

    folium.GeoJson(
        res, 
        style_function=lambda x: {'color': '#0000FF', 'weight': 2, 'fillOpacity': 0.2}
    ).add_to(m)
    
    st_folium(m, width=700, height=500, key="fixed_preview_map")

    c1, c2 = st.columns(2)
    geojson_out = res.to_json(na='null', show_bbox=False, drop_id=True)
    c1.download_button("Download GeoJSON", geojson_out, "snapped.geojson", use_container_width=True)
    
    res.to_file("temp_out.kml", driver='KML')
    with open("temp_out.kml", "rb") as f:
        c2.download_button("Download KML", f, "snapped.kml", use_container_width=True)
    os.remove("temp_out.kml")
