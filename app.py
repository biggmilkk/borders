# --- Export Section ---
@st.fragment
def export_section():
    if st.session_state.active_result is not None:
        st.markdown("---")
        st.subheader("Export Results")
        
        export_gdf = st.session_state.active_result.copy()
        clean_name = selected_target.lower().replace(" ", "_") if selected_target else "country"
        final_filename = f"{clean_name}_border"
        
        # We use a 3-column layout with specific ratios to force centering
        # or use a single container with custom CSS
        col_left, col_json, col_kml, col_right = st.columns([1, 2, 2, 1])
        
        with col_json:
            st.download_button(
                label="Download GeoJSON",
                data=export_gdf.to_json(),
                file_name=f"{final_filename}.geojson",
                mime="application/json",
                use_container_width=True  # Forces button to fill the column
            )
        
        with col_kml:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.kml') as tmp:
                    export_gdf.to_file(tmp.name, driver='KML')
                    with open(tmp.name, "rb") as f:
                        st.download_button(
                            label="Download KML",
                            data=f.read(),
                            file_name=f"{final_filename}.kml",
                            mime="application/vnd.google-earth.kml+xml",
                            use_container_width=True # Forces button to fill the column
                        )
                os.remove(tmp.name)
            except:
                st.error("KML export unavailable.")
        
        # Reset Button Row
        st.write("") # Spacer
        # Use columns to center the reset button as well
        _, reset_col, _ = st.columns([2, 1, 2])
        with reset_col:
            if st.button("Reset Canvas"):
                st.session_state.active_result = None
                st.rerun()
