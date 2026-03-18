import streamlit as st
import geopandas as gpd
import pycountry
import tempfile
import os
import fiona
from shapely.geometry import shape, Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw

# --- Configuration ---
st.set_page_config(page_title="Geospatial Border Alignment Engine", layout="centered")

WB_GPKG_PATH = "data/World_Bank_Official_Boundaries_Admin_0_all_layers.gpkg"
SHOW_DEBUG = True

# Initialize KML drivers
if "KML" not in fiona.supported_drivers:
    fiona.supported_drivers["KML"] = "rw"

# --- Styling ---
st.markdown("""
    <style>
    .main { background-color: #ffffff; }
    div.stButton > button {
        width: 100%;
        border-radius: 2px;
        height: 3.5em;
        background-color: #1a1a1a;
        color: white;
        border: none;
        margin-top: 10px;
    }
    div.stButton > button:hover {
        background-color: #333333;
        color: white;
    }
    #reset-button div.stButton > button {
        background-color: #f0f2f6;
        color: #1a1a1a;
        border: 1px solid #d1d5db;
    }
    div[data-testid="stOverlay"] {
        background-color: transparent !important;
        backdrop-filter: none !important;
    }
    .stAppViewMain {
        filter: none !important;
    }
    [data-testid="stStatusWidget"] {
        display: none;
    }
    .block-container {
        max-width: 900px;
        padding-top: 2rem;
    }
    </style>
""", unsafe_allow_html=True)


def get_country_iso3(country_name):
    if not country_name:
        return None

    country = pycountry.countries.get(name=country_name)
    if country:
        return country.alpha_3

    try:
        matches = pycountry.countries.search_fuzzy(country_name)
        if matches:
            return matches[0].alpha_3
    except Exception:
        pass

    return None


def detect_iso3_column(gdf):
    if gdf is None or gdf.empty:
        return None

    candidates = [
        "ISO3", "ISO_A3", "ADM0_A3", "WB_A3", "COUNTRY_ISO3",
        "ISO_3", "A3", "CNTRY_CODE", "COUNTRY_CODE", "ISO3CD",
        "ISO_CODE", "ADM0_CODE", "SOV_A3", "GU_A3"
    ]

    for col in candidates:
        if col in gdf.columns:
            return col

    for col in gdf.columns:
        try:
            series = gdf[col]
            if series.dtype == "object":
                sample = series.dropna().astype(str).head(100)
                if sample.empty:
                    continue

                hits = sum(
                    1 for v in sample
                    if len(v.strip()) == 3 and v.strip().isupper() and v.strip().isalpha()
                )
                if hits >= max(3, len(sample) // 2):
                    return col
        except Exception:
            continue

    return None


@st.cache_data(show_spinner=False)
def discover_world_bank_layers(gpkg_path):
    if not os.path.exists(gpkg_path):
        return []
    try:
        return list(fiona.listlayers(gpkg_path))
    except Exception:
        return []


@st.cache_data(show_spinner=False)
def load_candidate_layer(gpkg_path, layer_name):
    gdf = gpd.read_file(gpkg_path, layer=layer_name)

    if gdf is None or gdf.empty:
        return None

    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")

    return gdf


@st.cache_data(show_spinner=False)
def load_world_bank_admin0(gpkg_path):
    if not os.path.exists(gpkg_path):
        return None, None, None

    layers = discover_world_bank_layers(gpkg_path)
    if not layers:
        return None, None, None

    preferred_layers = []
    fallback_layers = []

    for layer in layers:
        lname = layer.lower()
        if "admin" in lname and "0" in lname:
            preferred_layers.append(layer)
        else:
            fallback_layers.append(layer)

    search_order = preferred_layers + fallback_layers

    for layer in search_order:
        try:
            gdf = load_candidate_layer(gpkg_path, layer)
            if gdf is None or gdf.empty:
                continue

            if "geometry" not in gdf.columns:
                continue

            geom_types = set(gdf.geometry.geom_type.dropna().unique())
            if not geom_types.intersection({"Polygon", "MultiPolygon"}):
                continue

            iso_col = detect_iso3_column(gdf)
            if not iso_col:
                continue

            return gdf, layer, iso_col
        except Exception:
            continue

    return None, None, None


@st.cache_data(show_spinner=False)
def fetch_boundary(country_name):
    if not country_name:
        return None

    iso3 = get_country_iso3(country_name)
    if not iso3:
        return None

    try:
        admin0_gdf, _, iso_col = load_world_bank_admin0(WB_GPKG_PATH)
        if admin0_gdf is None or not iso_col:
            return None

        result = admin0_gdf[
            admin0_gdf[iso_col].astype(str).str.strip().str.upper() == iso3
        ].copy()

        if result.empty:
            return None

        return result[["geometry"]]

    except Exception:
        return None


def flatten_to_multipolygon(geom):
    """
    Convert Polygon / MultiPolygon / GeometryCollection into a clean
    Polygon or MultiPolygon for stable preview + KML export.
    """
    if geom is None or geom.is_empty:
        return None

    try:
        geom = geom.buffer(0)
    except Exception:
        pass

    if geom.is_empty:
        return None

    if isinstance(geom, Polygon):
        return geom

    if isinstance(geom, MultiPolygon):
        return geom

    if isinstance(geom, GeometryCollection):
        polys = []
        for g in geom.geoms:
            if isinstance(g, Polygon):
                polys.append(g)
            elif isinstance(g, MultiPolygon):
                polys.extend(list(g.geoms))

        if not polys:
            return None

        if len(polys) == 1:
            return polys[0]
        return MultiPolygon(polys)

    return None


def merge_to_single_feature(gdf):
    """
    Always return a ONE-ROW GeoDataFrame suitable for single-layer KML export.
    """
    if gdf is None or gdf.empty:
        return None

    geom_series = gdf.geometry.dropna()
    if geom_series.empty:
        return None

    try:
        merged = geom_series.union_all()
    except Exception:
        merged = unary_union(list(geom_series))

    merged = flatten_to_multipolygon(merged)
    if merged is None or merged.is_empty:
        return None

    return gpd.GeoDataFrame(geometry=[merged], crs=gdf.crs)


# --- Persistence ---
if "active_result" not in st.session_state:
    st.session_state.active_result = None


# --- Header and Jurisdiction Selection ---
st.title("Geospatial Border Alignment Engine")
st.caption("Engineered spatial reconciliation of user-defined vectors against World Bank ADM0 datasets.")

if SHOW_DEBUG:
    with st.expander("World Bank GeoPackage Debug", expanded=False):
        st.write("Current working directory:", os.getcwd())
        st.write("GeoPackage path:", WB_GPKG_PATH)
        st.write("GeoPackage exists:", os.path.exists(WB_GPKG_PATH))

        layers = discover_world_bank_layers(WB_GPKG_PATH)
        st.write("Layers found:", layers if layers else "None")

        detected_gdf, detected_layer, detected_iso_col = load_world_bank_admin0(WB_GPKG_PATH)

        st.write("Detected Admin 0 layer:", detected_layer)
        st.write("Detected ISO3 column:", detected_iso_col)

        if detected_gdf is not None:
            st.write("Detected CRS:", detected_gdf.crs)
            st.write("Detected columns:", list(detected_gdf.columns))
            st.dataframe(detected_gdf.head(3))

if not os.path.exists(WB_GPKG_PATH):
    st.error(
        f"GeoPackage not found. Place '{WB_GPKG_PATH}' in the same folder as app.py "
        "or update WB_GPKG_PATH to the correct location."
    )

country_list = sorted([c.name for c in pycountry.countries])

selected_target = st.selectbox(
    "Select International Jurisdiction",
    country_list,
    index=None,
    placeholder="Choose a country to load reference borders..."
)

boundary_gdf = fetch_boundary(selected_target)


# --- Spatial Workbench (Map) ---
st.markdown("---")
st.subheader("Define Area of Interest")

if not selected_target:
    st.info("Select a jurisdiction above to activate the spatial workbench.")

if selected_target and boundary_gdf is None:
    st.warning("Could not load the selected country boundary from the World Bank GeoPackage.")

if boundary_gdf is not None:
    b = boundary_gdf.total_bounds
    map_center = [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2]
    m = folium.Map(location=map_center, zoom_start=6, tiles="CartoDB Positron")
    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])

    folium.GeoJson(
        boundary_gdf,
        style_function=lambda x: {
            "color": "#1a1a1a",
            "fillOpacity": 0.02,
            "weight": 0.8
        },
        interactive=False
    ).add_to(m)
else:
    m = folium.Map(location=[20, 0], zoom_start=2, tiles="CartoDB Positron")

# Preview result
if st.session_state.active_result is not None:
    folium.GeoJson(
        st.session_state.active_result,
        style_function=lambda x: {
            "color": "#0047AB",
            "fillColor": "#0047AB",
            "fillOpacity": 0.3,
            "weight": 2
        }
    ).add_to(m)

# Drawing tools
Draw(
    export=False,
    position="topleft",
    draw_options={
        "polyline": False,
        "circle": False,
        "marker": False,
        "circlemarker": False,
        "polygon": True,
        "rectangle": True
    }
).add_to(m)

map_interaction = st_folium(
    m,
    width="100%",
    height=550,
    key="workbench_map",
    returned_objects=["all_drawings"]
)

# --- Processing Logic ---
if map_interaction and map_interaction.get("all_drawings") and boundary_gdf is not None:
    latest_drawing = map_interaction["all_drawings"][-1]
    raw_shape = shape(latest_drawing["geometry"])

    if raw_shape.is_valid:
        input_gdf = gpd.GeoDataFrame(geometry=[raw_shape], crs="EPSG:4326")

        try:
            processed_intersection = gpd.overlay(input_gdf, boundary_gdf, how="intersection")

            if not processed_intersection.empty:
                final_gdf = merge_to_single_feature(processed_intersection)

                if final_gdf is not None:
                    if (
                        st.session_state.active_result is None
                        or not final_gdf.equals(st.session_state.active_result)
                    ):
                        st.session_state.active_result = final_gdf
                        st.rerun()

        except Exception as e:
            st.error(f"Spatial processing failed: {e}")


# --- Export Section ---
@st.fragment
def export_section():
    if st.session_state.active_result is not None:
        st.markdown("---")
        st.subheader("Export Results")

        export_gdf = merge_to_single_feature(st.session_state.active_result)

        if export_gdf is None or export_gdf.empty:
            st.error("No valid geometry available for export.")
            return

        clean_name = selected_target.lower().replace(" ", "_") if selected_target else "country"
        final_filename = f"{clean_name}_border"

        col_json, col_kml = st.columns(2)

        with col_json:
            st.download_button(
                label="Download GeoJSON",
                data=export_gdf.to_json(),
                file_name=f"{final_filename}.geojson",
                mime="application/json",
                use_container_width=True
            )

        with col_kml:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".kml") as tmp:
                    export_gdf.to_file(tmp.name, driver="KML")

                    with open(tmp.name, "rb") as f:
                        kml_data = f.read()

                st.download_button(
                    label="Download KML",
                    data=kml_data,
                    file_name=f"{final_filename}.kml",
                    mime="application/vnd.google-earth.kml+xml",
                    use_container_width=True
                )

                os.remove(tmp.name)

            except Exception as e:
                st.error(f"KML export failed: {e}")

        st.markdown('<div id="reset-button">', unsafe_allow_html=True)
        if st.button("Reset Canvas", use_container_width=True):
            st.session_state.active_result = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


export_section()
