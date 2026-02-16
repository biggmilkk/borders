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
from streamlit_folium import folium_static 
import folium

# Setup
fiona.supported_drivers['KML'] = 'rw'
st.set_page_config(page_title="Global Border Snapper", layout="centered")

if 'result_gdf' not in st.session_state:
    st.session_state.result_gdf = None

def get_iso3(name):
    try:
        return pycountry.countries.get(name=name).alpha_3
    except:
        return None

def load_data(file):
    fname = file.name.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fname)[1]) as tmp:
        tmp.write(file.getvalue())
        tmp_path = tmp.name
    try:
        if fname.endswith('.kmz'):
            with ZipFile(tmp_path, 'r') as kmz:
                kml_names = [f for f in kmz.namelist() if f.endswith('.kml')]
                if not kml_names: return None
                k_path = os.path.join(tempfile.gettempdir(), kml_names[0])
                kmz.extract(kml_names[0], path=tempfile.gettempdir())
                layers = fiona.listlayers(k_path)
                gdfs = []
                for l in layers:
                    try:
                        gdf = gpd.read_file(k_path, layer=l)
                        if not gdf.empty: gdfs.append(gdf)
                    except: continue
                return pd.concat(gdfs, ignore_index=True) if gdfs else None
        return gpd.read_file(tmp_path)
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

st.title("Global Border Snapper")
st.markdown("Selective snapping for Southern Switzerland KMZ files.")

with st.container(border=True):
    uploaded_file = st.file_uploader("Upload KMZ", type=['kmz', 'kml', 'geojson'])
    countries = sorted([c.name for c in pycountry.countries])
    selected_country = st.selectbox("Target Country", options=countries, index=countries.index("Switzerland") if "Switzerland" in countries else 0)
    snap_distance = st.slider("Snap Sensitivity (Magnet)", 0.001, 0.05, 0.015, format="%.3f")

    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.status("Processing...") as status:
                    iso_code = get_iso3(selected_country)
                    raw_data = load_data(uploaded_file)
                    
                    if raw_data is None or raw_data.empty:
                        st.error("No valid polygons found in file.")
                        st.stop()

                    # Convert and Flatten to 2D
                    user_gdf = raw_data.to_crs(epsg=4326)
                    user_gdf['geometry'] = user_gdf.geometry.map(lambda g: g.make_valid())
                    user_geom = user_gdf.geometry.union_all()

                    # Fetch Border
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    r = requests.get(api_url).json()
                    border_gdf = gpd.read_file(r['gjDownloadURL'])
                    border_geom = border_gdf.geometry.union_all()

                    # Selective Snap
                    final_union = user_geom
                    bnd = user_geom.boundary
                    if bnd is not None and not bnd.is_empty:
                        search_zone = bnd.buffer(snap_distance)
                        relevant_border = border_geom.intersection(search_zone)
                        if not relevant_border.is_empty:
                            final_union = user_geom.union(relevant_border)

                    # Final Cleanup
                    final_poly_geom = final_union.buffer(0.00001).buffer(-0.00001)
                    if isinstance(final_poly_geom, MultiPolygon):
                        final_poly = max(final_poly_geom.geoms, key=lambda a: a.area)
                    else:
                        final_poly = final_poly_geom

                    # Segmentize for Mapbox high-detail
                    final_poly = final_poly.segmentize(max_segment_length=0.0005)
                    st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                    status.update(label="Snap Complete!", state="complete")
            except Exception as e:
                st.error(f"Error: {e}")

# --- Results (Visible Even if Map Fails) ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    bounds = res.total_bounds
    
    st.divider()
    st.subheader("Preview and Export")
    
    if any(pd.isna(bounds)):
        st.error("The processed geometry is empty or invalid. Try increasing 'Snap Sensitivity'.")
    else:
        # 1. Map Render
        m = folium.Map(tiles='OpenStreetMap')
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        folium.GeoJson(res, style_function=lambda x: {'color': '#0000FF', 'weight': 2, 'fillOpacity': 0.2}).add_to(m)
        
        # Explicit width/height for visibility
        folium_static(m, width=700, height=500)

        # 2. Downloads
        c1, c2 = st.columns(2)
        c1.download_button("Download GeoJSON", res.to_json(), "snapped.geojson", use_container_width=True)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.kml') as tmp:
            res.to_file(tmp.name, driver='KML')
            with open(tmp.name, "rb") as f:
                c2.download_button("Download KML", f, "snapped.kml", use_container_width=True)
        os.remove(tmp.name)
