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

if 'result_gdf' not in st.session_state:
    st.session_state.result_gdf = None

def get_iso3(name):
    return pycountry.countries.get(name=name).alpha_3

def load_data(uploaded_file):
    fname = uploaded_file.name.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fname)[1]) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name
    try:
        if fname.endswith('.kmz'):
            with ZipFile(tmp_path, 'r') as kmz:
                kml_names = [f for f in kmz.namelist() if f.endswith('.kml')]
                if not kml_names: return None
                kmz.extract(kml_names[0], path=tempfile.gettempdir())
                extracted_kml = os.path.join(tempfile.gettempdir(), kml_names[0])
                layers = fiona.listlayers(extracted_kml)
                gdfs = [gpd.read_file(extracted_kml, layer=l) for l in layers]
                return gpd.pd.concat(gdfs, ignore_index=True)
        elif fname.endswith('.kml'):
            layers = fiona.listlayers(tmp_path)
            gdfs = [gpd.read_file(tmp_path, layer=l) for l in layers]
            return gpd.pd.concat(gdfs, ignore_index=True)
        else:
            return gpd.read_file(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

st.title("Global Border Snapper")

with st.container(border=True):
    uploaded_file = st.file_uploader("1. Upload Polygon", type=['geojson', 'kml', 'kmz'])
    countries = sorted([c.name for c in pycountry.countries])
    selected_country = st.selectbox("2. Target Country", options=countries)
    
    if st.button("Process and Snap", use_container_width=True):
        if uploaded_file:
            try:
                with st.spinner("Cleaning and Snapping..."):
                    iso_code = get_iso3(selected_country)
                    raw_gdf = load_data(uploaded_file)
                    
                    # Remove empty geometries immediately
                    raw_gdf = raw_gdf[raw_gdf.geometry.notnull()]
                    raw_gdf = raw_gdf[~raw_gdf.geometry.is_empty]
                    
                    if raw_gdf.empty:
                        st.error("The uploaded file contains no valid polygon data.")
                    else:
                        if raw_gdf.crs is None:
                            raw_gdf.set_crs(epsg=4326, inplace=True)
                        user_gdf = raw_gdf.to_crs(epsg=4326)
                        user_geom = user_gdf.geometry.union_all()

                        # Fetch Border
                        api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                        r = requests.get(api_url).json()
                        border_gdf = gpd.read_file(r['gjDownloadURL'])
                        border_geom = border_gdf.geometry.union_all()

                        # Geometric Merge
                        snapped_segment = user_geom.buffer(0.005).intersection(border_geom)
                        final_union = user_geom.union(snapped_segment)
                        
                        if isinstance(final_union, MultiPolygon):
                            final_poly = max(final_union.geoms, key=lambda a: a.area)
                        else:
                            final_poly = final_union

                        # Ensure we don't have a null result after intersection
                        if final_poly is None or final_poly.is_empty:
                            st.error("The snapping process resulted in an empty shape.")
                        else:
                            final_poly = final_poly.segmentize(max_segment_length=0.0005)
                            st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                            st.success("Processing Complete")
            except Exception as e:
                st.error(f"Error: {e}")

# --- Results Area ---
if st.session_state.result_gdf is not None:
    res_gdf = st.session_state.result_gdf
    
    # Final check for NaNs in bounds
    bounds = res_gdf.total_bounds
    if not any(pd.isna(bounds) for bounds in bounds):
        st.divider()
        st.subheader("Preview and Export")
        
        m = folium.Map(tiles='OpenStreetMap')
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        
        folium.GeoJson(
            res_gdf, 
            style_function=lambda x: {'color': '#0000FF', 'weight': 2, 'fillOpacity': 0.2}
        ).add_to(m)
        
        st_folium(m, width=700, height=500, key="preview_map")

        geojson_out = res_gdf.to_json(na='null', show_bbox=False, drop_id=True)
        st.download_button("Download GeoJSON", geojson_out, "snapped.geojson", use_container_width=True)
    else:
        st.error("Could not calculate map bounds. The geometry might be invalid.")
