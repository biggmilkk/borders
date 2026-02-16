import streamlit as st
import geopandas as gpd
import requests
import fiona
import os
import pycountry
import tempfile
import pandas as pd
from zipfile import ZipFile
from shapely.geometry import MultiPolygon, Polygon
from streamlit_folium import folium_static
import folium

# --- Setup ---
# Attempt to enable KML support in Fiona
try:
    fiona.supported_drivers['KML'] = 'rw'
    fiona.supported_drivers['LIBKML'] = 'rw'
except:
    pass

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
    suffix = os.path.splitext(fname)[1]
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file.getvalue())
        tmp_path = tmp.name
    
    try:
        if fname.endswith('.kmz'):
            with ZipFile(tmp_path, 'r') as kmz:
                # Find the main KML file inside the KMZ
                kml_names = [f for f in kmz.namelist() if f.endswith('.kml')]
                if not kml_names: return None
                
                # Extract KML to a temp directory to read
                extract_path = tempfile.gettempdir()
                kmz.extract(kml_names[0], path=extract_path)
                k_path = os.path.join(extract_path, kml_names[0])
                
                layers = fiona.listlayers(k_path)
                gdfs = []
                for l in layers:
                    gdf = gpd.read_file(k_path, layer=l)
                    if not gdf.empty: 
                        gdfs.append(gdf)
                return pd.concat(gdfs, ignore_index=True) if gdfs else None
        
        return gpd.read_file(tmp_path)
    except Exception as e:
        st.error(f"File Loading Error: {e}")
        return None
    finally:
        if os.path.exists(tmp_path): 
            os.remove(tmp_path)

# --- UI Layout ---
st.title("🗺️ Global Border Snapper")
st.markdown("Selectively snap your local geometries to official national borders.")

with st.container(border=True):
    uploaded_file = st.file_uploader("Upload KMZ, KML, or GeoJSON", type=['kmz', 'kml', 'geojson'])
    
    countries = sorted([c.name for c in pycountry.countries])
    default_idx = countries.index("Switzerland") if "Switzerland" in countries else 0
    selected_country = st.selectbox("Target Country for Snapping", options=countries, index=default_idx)
    
    # Sensitivity: Lower is safer for performance
    snap_distance = st.slider("Snap Sensitivity (Degrees)", 0.001, 0.05, 0.015, format="%.3f", 
                              help="How far the 'magnet' reaches to find the border.")

    if st.button("Process and Snap", use_container_width=True):
        if not uploaded_file:
            st.warning("Please upload a file first.")
        else:
            try:
                with st.status("Running Spatial Analysis...") as status:
                    # 1. Load and Clean User Data
                    iso_code = get_iso3(selected_country)
                    raw_data = load_data(uploaded_file)
                    
                    if raw_data is None or raw_data.empty:
                        st.error("No valid polygons found.")
                        st.stop()

                    user_gdf = raw_data.to_crs(epsg=4326)
                    # Merge all features into one shape and simplify slightly to prevent hanging
                    user_geom = user_gdf.geometry.union_all().make_valid().simplify(0.00001)

                    # 2. Fetch official Border from geoBoundaries API
                    status.update(label="Fetching Border Data...")
                    api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso_code}/ADM0/"
                    
                    resp = requests.get(api_url, timeout=15)
                    resp.raise_for_status()
                    border_data = resp.json()
                    
                    border_gdf = gpd.read_file(border_data['gjDownloadURL'])
                    border_geom = border_gdf.geometry.union_all().make_valid()

                    # 3. Selective Snapping Logic
                    status.update(label="Snapping Geometries...")
                    # Identify the area near the user's boundary
                    search_zone = user_geom.boundary.buffer(snap_distance)
                    relevant_border = border_geom.intersection(search_zone)

                    if not relevant_border.is_empty:
                        # Union the user shape with the border segment found in the search zone
                        final_union = user_geom.union(relevant_border)
                    else:
                        final_union = user_geom

                    # 4. Clean up Geometry
                    final_poly_geom = final_union.make_valid()
                    
                    # Ensure we have a single Polygon (pick largest if MultiPolygon)
                    if isinstance(final_poly_geom, (MultiPolygon)):
                        final_poly = max(final_poly_geom.geoms, key=lambda a: a.area)
                    else:
                        final_poly = final_poly_geom

                    # High detail segmentization (don't go too small or Folium will crash)
                    final_poly = final_poly.segmentize(max_segment_length=0.001)
                    
                    st.session_state.result_gdf = gpd.GeoDataFrame(geometry=[final_poly], crs="EPSG:4326")
                    status.update(label="Processing Complete!", state="complete")
                    
            except Exception as e:
                st.error(f"Processing Error: {str(e)}")

# --- Results Section ---
if st.session_state.result_gdf is not None:
    res = st.session_state.result_gdf
    bounds = res.total_bounds
    
    st.divider()
    st.subheader("Results")
    
    if any(pd.isna(bounds)):
        st.error("Resulting geometry is invalid. Try a different Snap Sensitivity.")
    else:
        # Create Folium Map
        m = folium.Map()
        # Fix for Folium bounds [lat, lon]
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        
        folium.GeoJson(
            res, 
            style_function=lambda x: {'color': '#2ecc71', 'fillColor': '#2ecc71', 'weight': 3, 'fillOpacity': 0.3}
        ).add_to(m)
        
        folium_static(m, width=700, height=450)

        # Download Buttons
        col1, col2 = st.columns(2)
        
        # GeoJSON Export
        col1.download_button(
            label="Download GeoJSON",
            data=res.to_json(),
            file_name="snapped_border.geojson",
            mime="application/json",
            use_container_width=True
        )
        
        # KML Export
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.kml') as tmp:
                res.to_file(tmp.name, driver='KML')
                with open(tmp.name, "rb") as f:
                    col2.download_button(
                        label="Download KML",
                        data=f,
                        file_name="snapped_border.kml",
                        use_container_width=True
                    )
            os.remove(tmp.name)
        except Exception as kml_err:
            col2.error("KML Export failed (Driver issue). Use GeoJSON.")
