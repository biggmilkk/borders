# app.py
"""
Border Snapper — snap & clip polygons to international borders (geoBoundaries)

Fixes:
- Avoids quantizing boundary geometries (which produced 'square' borders).
- Uses full geoBoundaries geometry by default (not simplified) unless user opts in.
- Applies precision/grid snapping only to subject/result geometries (optional).
- Keeps a single-feature (dissolved) output as requested.
"""

import streamlit as st
import tempfile
import io
import os
import json
import requests
import pycountry
from zipfile import ZipFile
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.ops import snap as shapely_snap
from shapely.validation import make_valid
from shapely import set_precision

import folium
from streamlit_folium import st_folium

# --------------------------
# Config
# --------------------------
st.set_page_config(page_title="Border Snapper (geoBoundaries)", layout="wide")

GEOBOUNDARIES_API_FMT = "https://www.geoboundaries.org/api/current/{release}/{iso3}/{adm}/"
CACHE_DIR = Path("data/geoboundaries_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

st.title("Border Snapper — clip polygons to international borders")
st.markdown(
    """
Upload a rough polygon and we'll **snap** it to the chosen border line and **clip** it so it doesn't cross into a neighboring country.

Defaults are conservative: we preserve boundary geometry fidelity. Advanced options let you sacrifice some precision to fix weird topology problems.
"""
)

# =========================
# Compatibility + geometry helpers
# =========================
def geom_union(geoms):
    try:
        return geoms.union_all()
    except Exception:
        return geoms.unary_union

def fix_geom(g, grid_size: float = 0.0, apply_precision: bool = True):
    """
    Fix geometry robustly.
      - apply_precision: if False, don't call set_precision (useful for boundary data).
      - grid_size in projected meters for set_precision (only used if apply_precision is True).
    """
    if g is None or getattr(g, "is_empty", False):
        return g

    if apply_precision and grid_size and grid_size > 0:
        try:
            g = set_precision(g, grid_size)
        except Exception:
            pass

    try:
        g2 = make_valid(g)
    except Exception:
        g2 = g

    try:
        g2 = g2.buffer(0)
    except Exception:
        pass

    return g2

def dissolve_to_single_feature(gdf: gpd.GeoDataFrame, precision_grid_m: float = 0.0) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        return gdf
    geom = geom_union(gdf.geometry)
    geom = fix_geom(geom, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([geom], crs=gdf.crs))

# =========================
# Country selection helpers
# =========================
def build_country_dropdown() -> Tuple[list, Dict[str, str]]:
    items = []
    for c in pycountry.countries:
        label = getattr(c, "common_name", None) or c.name
        items.append((label, c.alpha_3))
    dedup = {}
    for label, iso3 in items:
        dedup.setdefault(label, iso3)
    names = sorted(dedup.keys())
    return names, dedup

def country_name_to_iso3_offline(country_name: str) -> str:
    name = (country_name or "").strip()
    if not name:
        raise ValueError("Country name is empty.")
    hits = pycountry.countries.search_fuzzy(name)
    if hits and getattr(hits[0], "alpha_3", None):
        return hits[0].alpha_3
    raise ValueError(f"Could not resolve ISO3 for '{country_name}' using offline lookup.")

def country_name_to_iso3(country_name: str, allow_online_fallback: bool = False) -> str:
    try:
        return country_name_to_iso3_offline(country_name)
    except Exception:
        if not allow_online_fallback:
            raise
        name = (country_name or "").strip()
        url = f"https://restcountries.com/v3.1/name/{requests.utils.quote(name)}?fields=name,cca3"
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data and "cca3" in data[0]:
            return data[0]["cca3"]
        raise ValueError(f"Could not resolve ISO3 for '{country_name}' via online fallback.")

# =========================
# File readers
# =========================
def read_kmz_kml_bytes(file_bytes: bytes) -> str:
    try:
        with ZipFile(io.BytesIO(file_bytes)) as z:
            kml_files = [f for f in z.namelist() if f.lower().endswith(".kml")]
            if kml_files:
                return z.read(kml_files[0]).decode("utf-8")
    except Exception:
        pass
    try:
        return file_bytes.decode("utf-8")
    except Exception:
        raise ValueError("Uploaded KMZ/KML could not be read.")

def load_any_vector_file(uploaded) -> gpd.GeoDataFrame:
    suffix = Path(uploaded.name).suffix.lower()
    uploaded.seek(0)
    data = uploaded.read()

    if suffix in [".kmz", ".kml"]:
        kml_text = read_kmz_kml_bytes(data)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".kml")
        tmp.write(kml_text.encode("utf-8"))
        tmp.close()
        try:
            gdf = gpd.read_file(tmp.name, driver="KML")
        finally:
            os.unlink(tmp.name)
        return gdf

    if suffix in [".geojson", ".json"]:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(data)
        tmp.close()
        try:
            gdf = gpd.read_file(tmp.name)
        finally:
            os.unlink(tmp.name)
        return gdf

    if suffix == ".zip":
        tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_zip.write(data)
        tmp_zip.close()
        extract_dir = tempfile.mkdtemp()
        with ZipFile(tmp_zip.name, "r") as z:
            z.extractall(extract_dir)
        os.unlink(tmp_zip.name)

        shp_files = list(Path(extract_dir).glob("*.shp"))
        if not shp_files:
            raise ValueError("No shapefile (.shp) found inside uploaded zip.")
        return gpd.read_file(str(shp_files[0]))

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix if suffix else ".dat")
    tmp.write(data)
    tmp.close()
    try:
        return gpd.read_file(tmp.name)
    finally:
        os.unlink(tmp.name)

# =========================
# geoBoundaries loader + caching (prefer full geometry by default)
# =========================
@st.cache_data(show_spinner=False)
def fetch_geoboundary_metadata(iso3: str, adm_level: int = 0, release: str = "gbOpen") -> Dict[str, Any]:
    iso3 = iso3.upper()
    adm_tag = f"ADM{adm_level}"
    api_url = GEOBOUNDARIES_API_FMT.format(release=release, iso3=iso3, adm=adm_tag)
    r = requests.get(api_url, timeout=30)
    r.raise_for_status()
    return r.json()

@st.cache_data(show_spinner=False)
def download_geoboundary_to_cache(meta: Dict[str, Any],
                                  iso3: str,
                                  adm_level: int,
                                  release: str,
                                  prefer_simplified: bool = False) -> Tuple[str, str]:
    """
    prefer_simplified default = False to prefer accurate geometry.
    """
    iso3 = iso3.upper()
    adm_tag = f"ADM{adm_level}"
    cache_fname = CACHE_DIR / f"{iso3}_{adm_tag}_{release}.geojson"

    # Prefer full geometry first (accuracy). If user explicitly requests simplified, that path will be possible.
    urls = []
    if meta.get("gjDownloadURL"):
        urls.append(meta["gjDownloadURL"])
    if prefer_simplified and meta.get("gjDownloadURL_small"):
        urls.append(meta["gjDownloadURL_small"])
    if meta.get("shapeDownloadURL"):
        urls.append(meta["shapeDownloadURL"])

    urls = [u for u in urls if u]
    if not urls:
        raise RuntimeError("No GeoJSON download URL found in geoBoundaries metadata.")

    used_url = urls[0]
    if not cache_fname.exists():
        r = requests.get(used_url, timeout=90)
        r.raise_for_status()
        cache_fname.write_bytes(r.content)

    return str(cache_fname), used_url

def load_geoboundary_gdf(country_iso3: str,
                         adm_level: int,
                         release: str,
                         prefer_simplified: bool = False) -> Tuple[gpd.GeoDataFrame, Dict[str, Any]]:
    meta = fetch_geoboundary_metadata(country_iso3, adm_level, release)
    cache_path, used_url = download_geoboundary_to_cache(meta, country_iso3, adm_level, release, prefer_simplified)
    gdf = gpd.read_file(cache_path).to_crs("EPSG:4326")
    return gdf, {"meta": meta, "downloaded_url": used_url, "cache_file": cache_path}

# =========================
# Projection helper
# =========================
def choose_projected_crs(geom) -> str:
    try:
        lon, lat = geom.centroid.x, geom.centroid.y
        utm_zone = int((lon + 180) / 6) + 1
        epsg = (32600 + utm_zone) if lat >= 0 else (32700 + utm_zone)
        return f"EPSG:{epsg}"
    except Exception:
        return "EPSG:3857"

# =========================
# Snap & clip (boundary fidelity preserved)
# =========================
def snap_and_clip(subject_gdf: gpd.GeoDataFrame,
                  boundary_gdf: gpd.GeoDataFrame,
                  tolerance_m: float,
                  include_islands_within_m: float,
                  simplify_tol_m: Optional[float],
                  precision_grid_m: float = 0.0,
                  match_buffer_m: float = 5000.0) -> Tuple[gpd.GeoDataFrame, Dict[str, Any]]:
    """
    Apply snap & clip while preserving boundary geometry. Precision grid is only applied to subject/result.
    """
    subject_union = geom_union(subject_gdf.geometry)
    # Do NOT union the whole boundary in a way that destroys detail; we'll build lines from candidate features
    proj_crs = choose_projected_crs(subject_union)

    # Project for matching
    subject_proj_match = gpd.GeoSeries([subject_union], crs="EPSG:4326").to_crs(proj_crs).iloc[0]
    boundary_proj_match = boundary_gdf.to_crs(proj_crs)

    # Candidate boundary features by projected buffer
    match_geom = subject_proj_match.buffer(float(match_buffer_m))
    candidates_proj = boundary_proj_match[boundary_proj_match.geometry.intersects(match_geom)]
    if candidates_proj.empty:
        candidates_proj = boundary_proj_match[boundary_proj_match.geometry.contains(subject_proj_match.centroid)]
    if candidates_proj.empty:
        tmp = boundary_proj_match.copy()
        tmp["centroid_dist"] = tmp.geometry.centroid.distance(subject_proj_match.centroid)
        candidates_proj = tmp.sort_values("centroid_dist").head(1)

    # Use candidates (projected) to create the precise boundary lines for snapping
    candidates_union_proj = geom_union(candidates_proj.geometry)
    boundary_lines_proj = candidates_union_proj.boundary

    # Prepare subject (projected) and target polygon (projected)
    subject_proj = subject_proj_match
    target_proj = gpd.GeoSeries([geom_union(candidates_proj.to_crs("EPSG:4326").geometry)], crs="EPSG:4326").to_crs(proj_crs).iloc[0]

    # Fix subject/result geometries with optional precision; DO NOT apply precision to boundary lines (preserve detail)
    subject_proj = fix_geom(subject_proj, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))
    target_proj = fix_geom(target_proj, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))

    # Snap subject to boundary lines (boundary_lines_proj is high-fidelity)
    try:
        snapped = shapely_snap(subject_proj, boundary_lines_proj, float(tolerance_m))
    except Exception:
        snapped = shapely_snap(subject_proj, boundary_lines_proj, max(1.0, float(tolerance_m) / 5.0))
    snapped = fix_geom(snapped, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))

    # Clip to target polygon
    clipped = snapped.intersection(target_proj)
    clipped = fix_geom(clipped, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))

    # Optional islands/exclaves (done in projected CRS)
    island_included = False
    island_parts = 0
    if include_islands_within_m > 0:
        try:
            diff = fix_geom(target_proj, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0)).difference(
                fix_geom(clipped, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))
            )
            diff = fix_geom(diff, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))
            buffer_region = subject_proj.centroid.buffer(float(include_islands_within_m))
            extra_parts = []
            for part in getattr(diff, "geoms", [diff]):
                if part.intersects(buffer_region):
                    extra_parts.append(part)
            if extra_parts:
                clipped = unary_union([clipped] + extra_parts)
                clipped = fix_geom(clipped, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))
                island_included = True
                island_parts = len(extra_parts)
        except Exception:
            pass

    clipped_clean = fix_geom(clipped, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))
    if simplify_tol_m and simplify_tol_m > 0:
        clipped_clean = clipped_clean.simplify(float(simplify_tol_m), preserve_topology=True)
        clipped_clean = fix_geom(clipped_clean, grid_size=precision_grid_m, apply_precision=(precision_grid_m>0))

    # Return result in EPSG:4326
    result = gpd.GeoDataFrame(geometry=gpd.GeoSeries([clipped_clean], crs=proj_crs)).to_crs("EPSG:4326")

    report = {
        "proj_crs": proj_crs,
        "used_tolerance_m": float(tolerance_m),
        "include_islands_within_m": float(include_islands_within_m),
        "islands_included": island_included,
        "island_parts_added": island_parts,
        "simplify_tol_m": float(simplify_tol_m) if simplify_tol_m else 0.0,
        "precision_grid_m": float(precision_grid_m),
        "match_buffer_m": float(match_buffer_m),
        "input_features": int(len(subject_gdf)),
        "boundary_features": int(len(boundary_gdf)),
        "candidate_clip_features": int(len(candidates_proj)),
    }
    return result, report

# =========================
# KML export helper
# =========================
def gdf_to_simple_kml(gdf: gpd.GeoDataFrame) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>']
    for i, geom in enumerate(gdf.geometry):
        if geom.is_empty:
            continue
        if isinstance(geom, Polygon):
            polys = [geom]
        elif isinstance(geom, MultiPolygon):
            polys = list(geom.geoms)
        else:
            continue
        for p in polys:
            # FIX: Use c[:2] to ensure we only grab X and Y even if Z exists
            coords = " ".join([f"{c[0]},{c[1]},0" for c in p.exterior.coords])
            parts.append(
                f"<Placemark><name>snapped_{i}</name>"
                f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
                f"</Placemark>"
            )
    parts.append("</Document></kml>")
    return "\n".join(parts)

# =========================
# Map helpers
# =========================
def build_preview_map(center_latlon, original_json=None, boundary_json=None, result_json=None) -> folium.Map:
    m = folium.Map(location=center_latlon, zoom_start=6, control_scale=True)

    if original_json:
        folium.GeoJson(
            json.loads(original_json),
            name="Original polygon",
            style_function=lambda feat: {"color": "red", "weight": 2, "fillOpacity": 0.05},
        ).add_to(m)

    if boundary_json:
        folium.GeoJson(
            json.loads(boundary_json),
            name="Boundary polygons",
            style_function=lambda feat: {"color": "black", "weight": 1, "fillOpacity": 0.0},
        ).add_to(m)

    if result_json:
        folium.GeoJson(
            json.loads(result_json),
            name="Snapped & clipped",
            style_function=lambda feat: {"color": "green", "weight": 2, "fillOpacity": 0.2},
        ).add_to(m)

    folium.LayerControl().add_to(m)
    return m

# =========================
# UI
# =========================
col1, col2 = st.columns([2, 1])

with col1:
    st.header("1) Upload polygon")
    uploaded_poly = st.file_uploader(
        "Upload polygon (KML/KMZ/GeoJSON/zipped Shapefile)",
        type=["kml", "kmz", "geojson", "json", "zip"],
    )

    st.header("2) Choose boundaries")
    boundary_source = st.radio("Boundary source", options=["geoBoundaries (recommended)", "Upload boundary file"], index=0)

    uploaded_boundary = None
    if boundary_source == "Upload boundary file":
        uploaded_boundary = st.file_uploader(
            "Upload boundary file (GeoJSON/KML/KMZ/zipped Shapefile)",
            type=["kml", "kmz", "geojson", "json", "zip"],
        )

    st.markdown("---")
    st.header("3) Select country & border detail")

    country_names, name_to_iso3 = build_country_dropdown()

    search = st.text_input("Country search (type a few letters)", value="", placeholder="e.g., Singapore, United States")
    filtered = [n for n in country_names if search.lower() in n.lower()] if search else country_names
    if not search:
        st.caption("Tip: start typing in Country search to narrow the list.")
        filtered = filtered[:200]

    selected_country = st.selectbox("Country", options=filtered, index=0)
    resolved_iso3 = name_to_iso3.get(selected_country)

    detail_label = st.selectbox(
        "Border detail level",
        options=[
            "Country border (recommended)",
            "Region/state borders (more detailed)",
            "District/county borders (most detailed)",
        ],
        index=0,
    )

    if detail_label.startswith("Country"):
        adm_level = 0
    elif detail_label.startswith("Region"):
        adm_level = 1
    else:
        adm_level = 2

    release = st.selectbox("Dataset release", ["gbOpen", "gbHumanitarian", "gbAuthoritative"], index=0)
    # IMPORTANT: prefer_simplified default False to preserve accuracy
    prefer_simplified = st.checkbox("Use faster (simplified) boundaries (may be less accurate)", value=False)

    allow_online_fallback = st.checkbox(
        "Allow online name lookup fallback (rarely needed)",
        value=False,
    )

    st.caption(f"Selected: {selected_country} (ISO3: {resolved_iso3}) • Detail: {detail_label}")

    st.markdown("---")
    st.header("4) Run")
    run_btn = st.button("Run snap & clip", type="primary")

    with st.expander("Advanced geometry options (optional)", expanded=False):
        st.markdown(
            """
Defaults preserve accurate borders. Advanced options can resolve topology issues or reduce file size.

- **Snap tolerance (meters)** — how far points may move to meet the boundary. Smaller = less change.
- **Include islands/exclaves within (meters)** — optionally include nearby islands that belong to the country.
- **Simplify (meters)** — reduce vertex count after snapping. 0 = no simplification.
- **Precision cleanup grid (meters)** — **only** affects subject/result geometry; quantizes coordinates to reduce topology errors. **Warning:** larger values (>=0.1) can visibly simplify coastlines if applied to boundaries; by default we DO NOT apply it to boundaries.
- **Boundary match buffer (meters)** — how big a search area (meters) to find relevant boundary pieces; does not alter output shape.
"""
        )

        tol_m = st.slider("Snap tolerance (meters)", min_value=1, max_value=5000, value=250, step=1)
        preserve_islands_m = st.number_input("Include islands/exclaves within (meters) (0 = no)", min_value=0, value=0, step=50)
        simplify_m = st.number_input("Simplify (meters) (0 = no)", min_value=0, value=0, step=1)
        # default precision grid = 0.0 (off) to avoid blocky borders
        precision_grid_m = st.selectbox("Precision cleanup grid (meters) — subject/result only", options=[0.0, 0.01, 0.1, 1.0], index=0)
        match_buffer_m = st.number_input("Boundary match buffer (meters)", min_value=0, value=5000, step=1000)

    if "tol_m" not in locals():
        tol_m = 250
        preserve_islands_m = 0
        simplify_m = 0
        precision_grid_m = 0.0
        match_buffer_m = 5000

with col2:
    st.header("Quick help")
    st.markdown(
        """
**What you get**
- A corrected polygon that follows the chosen borders and does not cross into neighboring countries.
- Downloads: GeoJSON, KML, and the preview map (HTML).

**Recommended settings**
- Border detail: **Country border (recommended)**
- Snap tolerance: **50–300m**
- Islands: **0** unless required
- Leave Precision cleanup OFF unless you hit topology errors
"""
    )
    st.info("Attribution: geoBoundaries data is CC-BY 4.0 (William & Mary geoLab).")

# =========================
# Processing + preview storage
# =========================
def build_preview_map(center_latlon, original_json=None, boundary_json=None, result_json=None) -> folium.Map:
    m = folium.Map(location=center_latlon, zoom_start=6, control_scale=True)

    if original_json:
        folium.GeoJson(
            json.loads(original_json),
            name="Original polygon",
            style_function=lambda feat: {"color": "red", "weight": 2, "fillOpacity": 0.05},
        ).add_to(m)

    if boundary_json:
        folium.GeoJson(
            json.loads(boundary_json),
            name="Boundary polygons",
            style_function=lambda feat: {"color": "black", "weight": 1, "fillOpacity": 0.0},
        ).add_to(m)

    if result_json:
        folium.GeoJson(
            json.loads(result_json),
            name="Snapped & clipped",
            style_function=lambda feat: {"color": "green", "weight": 2, "fillOpacity": 0.2},
        ).add_to(m)

    folium.LayerControl().add_to(m)
    return m

if run_btn:
    if not uploaded_poly:
        st.error("Please upload a polygon file first.")
        st.stop()

    # 1) Load subject
    try:
        subj_gdf = load_any_vector_file(uploaded_poly)
    except Exception as e:
        st.exception(f"Failed to read polygon file: {e}")
        st.stop()

    subj_gdf = subj_gdf[subj_gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
    if subj_gdf.empty:
        st.error("No polygon geometries found in the uploaded polygon file.")
        st.stop()
    subj_gdf = subj_gdf.to_crs("EPSG:4326").copy()
    subj_gdf["geometry"] = subj_gdf["geometry"].apply(lambda g: fix_geom(g, grid_size=0.0, apply_precision=False))

    # 2) Load boundaries
    boundary_gdf = None
    geob_info = None

    if boundary_source == "geoBoundaries (recommended)":
        try:
            if not resolved_iso3:
                resolved_iso3 = country_name_to_iso3(selected_country, allow_online_fallback)

            # prefer_simplified default is False (preserve accuracy)
            boundary_gdf, geob_info = load_geoboundary_gdf(
                country_iso3=resolved_iso3,
                adm_level=int(adm_level),
                release=release,
                prefer_simplified=prefer_simplified,
            )
        except Exception as e:
            st.error(f"Failed to fetch geoBoundaries for {selected_country} (ISO3={resolved_iso3}): {e}")
            st.info("Try changing Dataset release, or use 'Upload boundary file'.")
            st.stop()
    else:
        if not uploaded_boundary:
            st.error("Please upload a boundary file.")
            st.stop()
        try:
            boundary_gdf = load_any_vector_file(uploaded_boundary).to_crs("EPSG:4326")
        except Exception as e:
            st.exception(f"Failed to read boundary file: {e}")
            st.stop()

    boundary_gdf = boundary_gdf[boundary_gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
    if boundary_gdf.empty:
        st.error("Boundary dataset contains no polygon geometries.")
        st.stop()
    # Keep boundary geometry high-fidelity: do not quantize it here
    boundary_gdf = boundary_gdf.copy()
    boundary_gdf["geometry"] = boundary_gdf["geometry"].apply(lambda g: fix_geom(g, grid_size=0.0, apply_precision=False))

    # 3) Snap & clip
    try:
        simpl_m = None if simplify_m == 0 else float(simplify_m)
        result_gdf, rpt = snap_and_clip(
            subject_gdf=subj_gdf,
            boundary_gdf=boundary_gdf,
            tolerance_m=float(tol_m),
            include_islands_within_m=float(preserve_islands_m),
            simplify_tol_m=simpl_m,
            precision_grid_m=float(precision_grid_m),
            match_buffer_m=float(match_buffer_m),
        )
    except Exception as e:
        st.error(f"Snap & clip failed: {e}")
        st.info("Try: tolerance 50–200m, islands=0, precision grid=0.01 or 0.1 (if needed).")
        st.stop()

    # 4) Dissolve result into one feature
    result_gdf = dissolve_to_single_feature(result_gdf, precision_grid_m=float(precision_grid_m))

    # 5) Persist outputs for preview/download
    centroid = geom_union(subj_gdf.geometry).centroid
    st.session_state["map_center"] = [centroid.y, centroid.x]
    st.session_state["map_original"] = subj_gdf.to_json()
    st.session_state["map_boundary"] = boundary_gdf.to_json()
    st.session_state["map_result"] = result_gdf.to_json()
    st.session_state["report"] = rpt
    st.session_state["result_geojson"] = result_gdf.to_json()
    st.session_state["result_kml"] = gdf_to_simple_kml(result_gdf)

    if geob_info:
        st.session_state["geob_info"] = geob_info
        st.session_state["country"] = selected_country
        st.session_state["iso3"] = resolved_iso3
        st.session_state["release"] = release
        st.session_state["detail_label"] = detail_label
        st.session_state["adm_level"] = int(adm_level)
    else:
        st.session_state.pop("geob_info", None)

# =========================
# Outputs
# =========================
if "map_result" in st.session_state:
    st.subheader("Report")
    rpt = dict(st.session_state.get("report", {}))
    if st.session_state.get("geob_info"):
        geob_info = st.session_state["geob_info"]
        meta = geob_info.get("meta", {})
        rpt.update({
            "country": st.session_state.get("country"),
            "detail_level": st.session_state.get("detail_label"),
            "iso3": st.session_state.get("iso3"),
            "geoboundaries_release": st.session_state.get("release"),
            "geoboundaries_downloaded_url": geob_info.get("downloaded_url"),
            "geoboundaries_cache_file": geob_info.get("cache_file"),
            "boundarySource": meta.get("boundarySource"),
            "boundaryYear": meta.get("boundaryYear"),
            "boundaryType": meta.get("boundaryType"),
            "boundaryISO": meta.get("boundaryISO"),
        })

    rpt["output_features"] = 1
    st.json(rpt)

    st.subheader("Preview map")
    m = build_preview_map(
        center_latlon=st.session_state["map_center"],
        original_json=st.session_state.get("map_original"),
        boundary_json=st.session_state.get("map_boundary"),
        result_json=st.session_state.get("map_result"),
    )

    st_folium(m, width=900, height=600, key="preview_map")

    st.markdown("### Download map")
    try:
        map_html = m.get_root().render().encode("utf-8")
        st.download_button(
            "Download preview map (HTML)",
            data=map_html,
            file_name="border_snap_preview_map.html",
            mime="text/html",
        )
    except Exception as e:
        st.warning(f"Could not prepare HTML map download: {e}")

    st.markdown("### Download outputs")
    st.download_button(
        "Download snapped polygon (GeoJSON)",
        data=st.session_state["result_geojson"].encode("utf-8"),
        file_name="snapped_clipped.geojson",
        mime="application/geo+json",
    )
    st.download_button(
        "Download snapped polygon (KML)",
        data=st.session_state["result_kml"].encode("utf-8"),
        file_name="snapped_clipped.kml",
        mime="application/vnd.google-earth.kml+xml",
    )
else:
    st.info("Upload a polygon and click Run to see results.")
