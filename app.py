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

# 1. Setup drivers and Page Config
fiona.supported_drivers['KML'] = 'rw'
st.set_page_config(page_title="Global Border Snapper", layout="centered")

# 2. Global Definition for Selectbox
countries_list = sorted([c.name for c in pycountry.countries])

if 'result_gdf' not in st.session_state:
    st.session_state.result_gdf = None

# --- Helpers ---
def get_iso3(name):
    try:
        return pycountry.countries.get(name=name).alpha_3
    except:
        return None

def load_data(file):
    fname = file.name.lower()
    # Use a physical temp file to prevent 'vsimem' driver errors
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fname)[1]) as tmp:
        tmp.write(file.getvalue())
        tmp_path = tmp.name

    try:
        if fname.endswith('.kmz'):
            with ZipFile(tmp_path, 'r') as kmz:
                # Find the internal KML file
                kml_name = [f for f in kmz.namelist() if f.endswith('.kml')][0]
                kmz.extract(kml_name, path=tempfile.gettempdir())
                extracted_kml = os.path.join(tempfile.gettempdir(), kml_name)
                
                # KMZs often have multiple layers; we merge them all
                layers = fiona.listlayers(extracted_kml)
                gdfs = [gpd.read_file(extracted_kml, layer=l) for l in layers]
                return gpd.pd.concat(gdfs, ignore_index=True)
        else:
            return gpd.read_file(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# --- Main UI ---
st.title("Global Border Snapper")
st.markdown("Selective snapping: Magnetize edges near borders while keeping internal lines intact.")

with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload KMZ (Southern Switzerland)", type=['geojson', 'kml', 'kmz'])
    # Default to Switzerland for your test
    default_ix = countries_list.index("Switzerland") if "Switzerland" in countries_list else 0
    selected_country = st.selectbox("2. Target Country", options=countries_list, index=default_ix)
    
    # Sensitivity slider: 0.01 is usually enough to 'grab' the Swiss-Italian border
    snap_distance = st.slider("Snap Sensitivity (Degrees)", 0.001, 0.05, 0.01, help="Higher = grabs borders further away.")

    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.status("Processing Southern Switzerland...") as status:
                    iso_code = get_iso3(selected_country)
                    
                    # 1. Load and Fix User Data
                    user_gdf = load_data(uploaded_file).to_crs(epsg=4326)
                    user_gdf['geometry'] = user_gdf.geometry.make_valid()
                    user_geom = user_gdf.geometry.union_all()

                    # 2. Fetch official border (Switzerland ADM0)
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    r = requests.get(api_url).json()
                    border_gdf = gpd.read_file(r['gjDownloadURL'])
                    border_geom = border_gdf.geometry.union_all()

                    # 3. SELECTIVE SNAP LOGIC
                    # Look for borders only near the perimeter of your half-country polygon
                    search_zone = user_geom.boundary.buffer(snap_distance)
                    relevant_border = border_geom.intersection(search_zone)
                    
                    if relevant_border is not None and not relevant_border.is_empty:
                        # Merges the Italian/French/Austrian border segments into your polygon
                        final_union = user_geom.union(relevant_border)
                    else:
                        final_union = user_geom
                    
                    # Cleanup slivers
                    final_poly_geom = final_union.buffer(0.00001).buffer(-0.00001)

                    if isinstance(final_poly_geom, MultiPolygon):
                        final_poly = max(final_poly_geom.geoms, key=lambda a: a.area)
                    else:
                        final_poly = final_poly_geom

                    # 4. Mapbox Optimization
                    final_poly = final_poly.segmentize(max_segment_length=0.0005)

                    st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                    status.update(label="Snap Complete!", state="complete")
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            st.warning("Please upload the Swiss KMZ file first.")

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
        st_folium(m, width=700, height=500, key="swiss_map")

        c1, c2 = st.columns(2)
        geojson_out = res.to_json()
        c1.download_button("Download GeoJSON", geojson_out, "snapped_switzerland.geojson", use_container_width=True)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.kml') as tmp:
            res.to_file(tmp.name, driver='KML')
            with open(tmp.name, "rb") as f:
                c2.download_button("Download KML", f, "snapped_switzerland.kml", use_container_width=True)
        os.remove(tmp.name)
