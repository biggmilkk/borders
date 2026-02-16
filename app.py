import streamlit as st
import geopandas as gpd
import requests
import fiona
import os
import pycountry
import tempfile
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

# --- Helpers ---
countries = sorted([c.name for c in pycountry.countries])

def get_iso3(name):
    return pycountry.countries.get(name=name).alpha_3

def load_data(uploaded_file):
    fname = uploaded_file.name.lower()
    
    # Use a temporary physical file to avoid driver/vsimem errors
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fname)[1]) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        if fname.endswith('.kmz'):
            with ZipFile(tmp_path, 'r') as kmz:
                kml_name = [f for f in kmz.namelist() if f.endswith('.kml')][0]
                kmz.extract(kml_name, path=tempfile.gettempdir())
                extracted_kml = os.path.join(tempfile.gettempdir(), kml_name)
                
                layers = fiona.listlayers(extracted_kml)
                gdfs = [gpd.read_file(extracted_kml, layer=l, driver='KML') for l in layers]
                return gpd.pd.concat(gdfs, ignore_index=True)
        
        elif fname.endswith('.kml'):
            layers = fiona.listlayers(tmp_path)
            gdfs = [gpd.read_file(tmp_path, layer=l, driver='KML') for l in layers]
            return gpd.pd.concat(gdfs, ignore_index=True)
        
        else:
            return gpd.read_file(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# --- Main UI ---
st.title("Global Border Snapper")
st.markdown("Snap polygons to international borders. Optimized for high-detail Mapbox uploads.")

with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload Polygon (GeoJSON, KML, KMZ)", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("2. Target Country", options=countries)
    
    # 0.0005 degrees (~50m) prevents Mapbox from simplifying complex border edges
    point_density = 0.0005 

    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.status("Processing geospatial data...") as status:
                    iso_code = get_iso3(selected_country)
                    
                    # 1. Load User Data
                    raw_gdf = load_data(uploaded_file)
                    if raw_gdf.crs is None:
                        raw_gdf.set_crs(epsg=4326, inplace=True)
                    user_gdf = raw_gdf.to_crs(epsg=4326)
                    
                    # Modern replacement for unary_union
                    user_geom = user_gdf.geometry.union_all()

                    # 2. Fetch geoBoundaries
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    r = requests.get(api_url).json()
                    border_gdf = gpd.read_file(r['gjDownloadURL'])
                    border_geom = border_gdf.geometry.union_all()

                    # 3. Snap and Merge Logic
                    snapped_segment = user_geom.buffer(0.005).intersection(border_geom)
                    final_union = user_geom.union(snapped_segment)
                    
                    # One contiguous polygon: take largest part
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

# --- Persistent Results Area ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    
    st.divider()
    st.subheader("Preview and Export")
    
    # Bounds for auto-fit
    bounds = res.total_bounds
    map_bounds = [[bounds
