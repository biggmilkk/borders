import streamlit as st
import geopandas as gpd
import requests
import fiona
import os
import pycountry
import tempfile
import pandas as pd
from zipfile import ZipFile
from shapely.geometry import MultiPolygon
from streamlit_folium import st_folium
import folium

# Setup
fiona.supported_drivers['KML'] = 'rw'
st.set_page_config(page_title="Global Border Snapper", layout="centered")
countries_list = sorted([c.name for c in pycountry.countries])

if 'result_gdf' not in st.session_state:
    st.session_state.result_gdf = None

def load_data(file):
    fname = file.name.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fname)[1]) as tmp:
        tmp.write(file.getvalue())
        tmp_path = tmp.name
    try:
        if fname.endswith('.kmz'):
            with ZipFile(tmp_path, 'r') as kmz:
                kml_name = [f for f in kmz.namelist() if f.endswith('.kml')][0]
                kmz.extract(kml_name, path=tempfile.gettempdir())
                extracted_kml = os.path.join(tempfile.gettempdir(), kml_name)
                layers = fiona.listlayers(extracted_kml)
                gdfs = []
                for l in layers:
                    gdf = gpd.read_file(extracted_kml, layer=l)
                    if not gdf.empty: gdfs.append(gdf)
                return gpd.pd.concat(gdfs, ignore_index=True)
        return gpd.read_file(tmp_path)
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

st.title("Global Border Snapper")

with st.container(border=True):
    uploaded_file = st.file_uploader("Upload KMZ", type=['geojson', 'kml', 'kmz'])
    selected_country = st.selectbox("Target Country", options=countries_list, index=countries_list.index("Switzerland"))
    snap_distance = st.slider("Snap Sensitivity", 0.001, 0.05, 0.01)

    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.status("Snapping Southern Switzerland...") as status:
                    iso_code = pycountry.countries.get(name=selected_country).alpha_3
                    user_gdf = load_data(uploaded_file).to_crs(epsg=4326)
                    user_gdf['geometry'] = user_gdf.geometry.make_valid()
                    user_geom = user_gdf.geometry.union_all()

                    # Fetch Border
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    r = requests.get(api_url).json()
                    border_gdf = gpd.read_file(r['gjDownloadURL'])
                    border_geom = border_gdf.geometry.union_all()

                    # --- FAIL-SAFE SNAPPING ---
                    final_union = user_geom
                    if user_geom is not None and not user_geom.is_empty:
                        # Only buffer if boundary exists
                        bnd = user_geom.boundary
                        if bnd is not None and not bnd.is_empty:
                            search_zone = bnd.buffer(snap_distance)
                            relevant_border = border_geom.intersection(search_zone)
                            
                            if relevant_border is not None and not relevant_border.is_empty:
                                final_union = user_geom.union(relevant_border)

                    # Final Cleanup
                    final_poly_geom = final_union.buffer(0.00001).buffer(-0.00001)
                    if isinstance(final_poly_geom, MultiPolygon):
                        final_poly = max(final_poly_geom.geoms, key=lambda a: a.area)
                    else:
                        final_poly = final_poly_geom

                    # Mapbox Detail
                    final_poly = final_poly.segmentize(max_segment_length=0.0005)
                    st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                    status.update(label="Complete!", state="complete")
            except Exception as e:
                st.error(f"Error: {e}")

# --- Results ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    bounds = res.total_bounds
    if not any(pd.isna(bounds)):
        st.divider()
        m = folium.Map(tiles='OpenStreetMap')
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        folium.GeoJson(res, style_function=lambda x: {'color': '#0000FF', 'weight': 2, 'fillOpacity': 0.2}).add_to(m)
        st_folium(m, width=700, height=500, key="swiss_map")
        
        c1, c2 = st.columns(2)
        c1.download_button("Download GeoJSON", res.to_json(), "snapped.geojson", use_container_width=True)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.kml') as tmp:
            res.to_file(tmp.name, driver='KML')
            with open(tmp.name, "rb") as f:
                c2.download_button("Download KML", f, "snapped.kml", use_container_width=True)
