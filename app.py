import re
import math
import html
from datetime import datetime
from io import BytesIO

import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st

st.set_page_config(
    page_title="Cloud PCB Design Assistant",
    page_icon="🔌",
    layout="wide"
)


# ============================================================
# 1. HELPER FUNCTIONS
# ============================================================

def extract_blocks(text, block_name):
    """Extract balanced KiCad S-expression blocks."""
    blocks = []
    pattern = re.compile(r"\(" + re.escape(block_name) + r"\b")
    position = 0

    while True:
        match = pattern.search(text, position)
        if not match:
            break

        start = match.start()
        depth = 0
        in_string = False
        escaped = False
        end = None

        for i in range(start, len(text)):
            char = text[i]

            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end is None:
            break

        blocks.append(text[start:end])
        position = end

    return blocks


def parse_pcb(text):
    """Extract component references, track segments and vias."""
    components = sorted(set(
        re.findall(
            r'\(property\s+"Reference"\s+"([^"\n]+)',
            text
        )
    ))

    components = [
        ref for ref in components
        if ref and ref not in {"REF**", ""}
    ]

    tracks = []

    for block in extract_blocks(text, "segment"):
        start = re.search(
            r"\(start\s+([-+\d.eE]+)\s+([-+\d.eE]+)",
            block
        )
        end = re.search(
            r"\(end\s+([-+\d.eE]+)\s+([-+\d.eE]+)",
            block
        )
        width = re.search(
            r"\(width\s+([-+\d.eE]+)\)",
            block
        )
        layer = re.search(
            r'\(layer\s+"([^"]+)"',
            block
        )

        if not start or not end:
            continue

        x1, y1 = map(float, start.groups())
        x2, y2 = map(float, end.groups())

        tracks.append({
            "Track": len(tracks) + 1,
            "Width (mm)": float(width.group(1)) if width else None,
            "Length (mm)": round(
                math.hypot(x2 - x1, y2 - y1), 3
            ),
            "Layer": layer.group(1) if layer else "Unknown",
            "X1": x1,
            "Y1": y1,
            "X2": x2,
            "Y2": y2,
        })

    return components, tracks, len(extract_blocks(text, "via"))


def parse_drc_report(report_text):
    """Parse a text-format KiCad DRC report."""
    result = {
        "Total violations": None,
        "Errors": 0,
        "Warnings": 0,
        "Unconnected pads": None,
        "Footprint errors": None,
        "Violations": []
    }

    count_patterns = {
        "Total violations": (
            r"\*\*\s*Found\s+(\d+)\s+DRC violations?\s*\*\*"
        ),
        "Unconnected pads": (
            r"\*\*\s*Found\s+(\d+)\s+unconnected pads?\s*\*\*"
        ),
        "Footprint errors": (
            r"\*\*\s*Found\s+(\d+)\s+Footprint errors?\s*\*\*"
        ),
    }

    for key, pattern in count_patterns.items():
        matches = re.findall(
            pattern,
            report_text,
            flags=re.IGNORECASE
        )
        if matches:
            result[key] = int(matches[-1])

    blocks = re.split(
        r"(?=^\[[^\]]+\]:)",
        report_text,
        flags=re.MULTILINE
    )

    for block in blocks:
        match = re.match(
            r"\[([^\]]+)\]:\s*(.+)",
            block.strip()
        )

        if not match:
            continue

        vtype = match.group(1).strip()
        description = match.group(2).strip().splitlines()[0]

        rule_match = re.search(
            r"Rule:.*?;\s*(error|warning)\b",
            block,
            flags=re.IGNORECASE
        )

        severity = (
            rule_match.group(1).capitalize()
            if rule_match
            else "Unspecified"
        )

        if severity == "Error":
            result["Errors"] += 1
        elif severity == "Warning":
            result["Warnings"] += 1

        details = [
            line.strip().lstrip("@").strip()
            for line in block.splitlines()[1:]
            if line.strip().startswith("@")
        ]

        result["Violations"].append({
            "Type": vtype,
            "Description": description,
            "Severity": severity,
            "Details": " | ".join(details)
        })

    return result


def calculate_review_risk(df, threshold, drc):
    """
    Calculate a rule-based review-priority score.
    This is not an AI prediction or safety certification.
    """
    total_tracks = len(df)

    flagged = (
        int((df["Review Flag"] != "No width flag").sum())
        if total_tracks else 0
    )

    ratio = flagged / total_tracks if total_tracks else 0
    score = min(40.0, ratio * 40.0)

    reasons = []
    actions = []

    if flagged:
        reasons.append(
            f"{flagged} of {total_tracks} track segments "
            f"({ratio * 100:.1f}%) are flagged for width review."
        )

        actions.append(
            f"Review the {flagged} flagged track segment(s) against "
            "net classes, current requirements, and fabrication rules."
        )
    else:
        reasons.append(
            f"No track segments were below the selected "
            f"{threshold:.2f} mm screening threshold."
        )

    coverage = "Partial — no DRC report supplied"

    if drc is None:
        actions.append(
            "Run KiCad Design Rules Checker and upload its .rpt "
            "or .txt report for additional rule-based review."
        )
    else:
        coverage = "Includes uploaded KiCad DRC report"

        errors = int(drc.get("Errors", 0) or 0)
        warnings = int(drc.get("Warnings", 0) or 0)
        unconnected = drc.get("Unconnected pads")
        footprint_errors = drc.get("Footprint errors")
        total = drc.get("Total violations")

        score += min(40.0, errors * 20.0)
        score += min(24.0, warnings * 8.0)

        if total and not errors and not warnings:
            score += min(24.0, int(total) * 8.0)

            reasons.append(
                f"The report lists {int(total)} violation(s), "
                "but the parser could not classify their severity."
            )

            actions.append(
                "Review the original DRC report because the parser "
                "could not classify every violation."
            )

        if errors:
            reasons.append(
                f"The uploaded DRC report identifies {errors} error(s)."
            )
            actions.append(
                "Resolve DRC errors, rerun DRC, and review the updated report."
            )

        if warnings:
            reasons.append(
                f"The uploaded DRC report identifies {warnings} warning(s)."
            )
            actions.append(
                "Review each DRC warning and decide whether it needs "
                "correction or is acceptable for the design."
            )

        if unconnected is not None:
            score += min(20.0, int(unconnected) * 10.0)

            if int(unconnected) > 0:
                reasons.append(
                    f"The report lists {int(unconnected)} unconnected pad(s)."
                )
                actions.append(
                    "Inspect unconnected pads and complete or validate "
                    "the intended connections."
                )

        if footprint_errors is not None:
            score += min(20.0, int(footprint_errors) * 10.0)

            if int(footprint_errors) > 0:
                reasons.append(
                    f"The report lists {int(footprint_errors)} footprint error(s)."
                )
                actions.append(
                    "Inspect footprint errors, pad mapping, and footprint "
                    "placement in KiCad."
                )

        if (
            not errors
            and not warnings
            and unconnected in (0, None)
            and footprint_errors in (0, None)
        ):
            reasons.append(
                "No DRC errors or warnings were counted by the parser; "
                "check the original report for completeness."
            )

    score = int(round(min(100.0, max(0.0, score))))
    level = "Low" if score < 25 else "Medium" if score < 60 else "High"

    if not actions:
        actions.append(
            "Continue reviewing the board against its project-specific "
            "electrical and manufacturing requirements."
        )

    return {
        "score": score,
        "level": level,
        "coverage": coverage,
        "reasons": reasons,
        "recommendations": actions
    }


# ============================================================
# 2. ROUTING DENSITY CALCULATION
# ============================================================

def calculate_routing_density(tracks, divisions=10):
    """
    Estimate routing density by counting distinct track segments
    passing through each grid cell.

    This is a geometry-based proxy, not copper-area analysis or DRC.
    """
    if not tracks:
        empty = pd.DataFrame()
        return empty, empty, empty, [], (0, 1, 0, 1)

    xs = [
        float(track[key])
        for track in tracks
        for key in ("X1", "X2")
    ]

    ys = [
        float(track[key])
        for track in tracks
        for key in ("Y1", "Y2")
    ]

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    # Avoid zero-sized ranges for horizontal or vertical-only tracks.
    if x_max == x_min:
        x_min -= 0.5
        x_max += 0.5

    if y_max == y_min:
        y_min -= 0.5
        y_max += 0.5

    cell_w = (x_max - x_min) / divisions
    cell_h = (y_max - y_min) / divisions

    counts = [
        [0 for _ in range(divisions)]
        for _ in range(divisions)
    ]

    for track in tracks:
        x1 = float(track["X1"])
        y1 = float(track["Y1"])
        x2 = float(track["X2"])
        y2 = float(track["Y2"])

        length = math.hypot(x2 - x1, y2 - y1)

        step_size = max(
            0.01,
            min(cell_w, cell_h) / 2
        )

        samples = max(
            2,
            min(300, int(length / step_size) + 2)
        )

        visited = set()

        for idx in range(samples):
            fraction = idx / (samples - 1)

            x = x1 + (x2 - x1) * fraction
            y = y1 + (y2 - y1) * fraction

            col = min(
                divisions - 1,
                max(0, int((x - x_min) / cell_w))
            )

            row = min(
                divisions - 1,
                max(0, int((y - y_min) / cell_h))
            )

            visited.add((row, col))

        for row, col in visited:
            counts[row][col] += 1

    cell_rows = []

    for row in range(divisions):
        for col in range(divisions):
            cell_rows.append({
                "Grid row": row + 1,
                "Grid column": col + 1,
                "X start (mm)": round(
                    x_min + col * cell_w, 3
                ),
                "X end (mm)": round(
                    x_min + (col + 1) * cell_w, 3
                ),
                "Y start (mm)": round(
                    y_min + row * cell_h, 3
                ),
                "Y end (mm)": round(
                    y_min + (row + 1) * cell_h, 3
                ),
                "Segment occupancy": counts[row][col],
            })

    grid_df = pd.DataFrame(cell_rows)

    occupied = [
        value
        for row in counts
        for value in row
        if value > 0
    ]

    if occupied:
        sorted_values = sorted(occupied)

        percentile_index = min(
            len(sorted_values) - 1,
            math.ceil(0.90 * len(sorted_values)) - 1
        )

        high_threshold = sorted_values[percentile_index]

        hotspots = grid_df[
            (grid_df["Segment occupancy"] >= max(2, high_threshold))
            & (grid_df["Segment occupancy"] > 0)
        ].copy()

        hotspots = hotspots.sort_values(
            "Segment occupancy",
            ascending=False
        ).reset_index(drop=True)
    else:
        hotspots = pd.DataFrame(columns=grid_df.columns)

    layer_df = (
        pd.DataFrame(tracks)
        .groupby("Layer", dropna=False)
        .agg(
            **{
                "Track segments": ("Track", "count"),
                "Total track length (mm)": ("Length (mm)", "sum"),
                "Average width (mm)": ("Width (mm)", "mean"),
            }
        )
        .reset_index()
    )

    layer_df["Total track length (mm)"] = (
        layer_df["Total track length (mm)"].round(3)
    )

    layer_df["Average width (mm)"] = (
        layer_df["Average width (mm)"].round(3)
    )

    return (
        grid_df,
        layer_df,
        hotspots,
        counts,
        (x_min, x_max, y_min, y_max)
    )


# ============================================================
# 3. PAGE HEADER AND SETTINGS
# ============================================================

st.title("🔌 Cloud-Based PCB Design Assistant")

st.write(
    "Analyze PCB geometry, review track widths, inspect KiCad DRC "
    "results, and download a combined report."
)

st.info(
    "Prototype notice: width flags are screening indicators, not proof "
    "of a violation. DRC results are imported from a report; this app "
    "does not currently run the KiCad DRC engine. The review-priority "
    "score is rule-based, not AI/ML."
)

with st.sidebar:
    st.header("Analysis Settings")

    min_width = st.number_input(
        "Flag tracks narrower than (mm)",
        min_value=0.01,
        max_value=10.0,
        value=0.20,
        step=0.05,
        help="User-selected screening threshold, not a universal limit."
    )

    st.caption(
        "Use actual board requirements when known. "
        "The default is for demonstration only."
    )


# ============================================================
# 4. DRC REPORT UPLOAD
# ============================================================

st.header("1. KiCad DRC Report")

st.write(
    "In KiCad, open Inspect → Design Rules Checker, run DRC, "
    "save the report, then upload the .rpt or .txt file here."
)

drc_file = st.file_uploader(
    "Upload KiCad DRC report (.rpt or .txt)",
    type=["rpt", "txt"],
    key="drc_report_upload"
)

drc_results = None
drc_text = None

if drc_file is not None:
    try:
        drc_text = drc_file.getvalue().decode("utf-8-sig")
        drc_results = parse_drc_report(drc_text)

        st.subheader("DRC Report Summary")
        st.caption(f"Report file: {drc_file.name}")

        a, b, c = st.columns(3)

        a.metric(
            "Total violations",
            drc_results["Total violations"]
            if drc_results["Total violations"] is not None
            else "Not identified"
        )

        b.metric(
            "Errors identified",
            drc_results["Errors"]
        )

        c.metric(
            "Warnings identified",
            drc_results["Warnings"]
        )

        if drc_results["Total violations"] == 0:
            st.success("The report indicates zero DRC violations.")
        elif drc_results["Total violations"] is not None:
            st.warning(
                f"KiCad reported {drc_results['Total violations']} "
                "DRC violation(s). Review the details below."
            )
        else:
            st.info(
                "The total count could not be identified automatically. "
                "Check the original report below."
            )

        a, b = st.columns(2)

        a.metric(
            "Unconnected pads",
            drc_results["Unconnected pads"]
            if drc_results["Unconnected pads"] is not None
            else "Not reported"
        )

        b.metric(
            "Footprint errors",
            drc_results["Footprint errors"]
            if drc_results["Footprint errors"] is not None
            else "Not reported"
        )

        st.subheader("Individual DRC Violations")

        if drc_results["Violations"]:
            violation_df = pd.DataFrame(
                drc_results["Violations"]
            )

            st.dataframe(
                violation_df,
                use_container_width=True,
                hide_index=True
            )

            st.download_button(
                "Download DRC violations as CSV",
                violation_df.to_csv(index=False).encode("utf-8"),
                "pcb_drc_violations.csv",
                "text/csv",
                key="download_drc_csv"
            )

        elif drc_results["Total violations"] == 0:
            st.success("No individual violations were listed.")
        else:
            st.info(
                "No individual violation entries were recognized. "
                "See the original report."
            )

        with st.expander("View full original DRC report"):
            st.code(drc_text, language="text")

        st.download_button(
            "Download original DRC report",
            drc_text.encode("utf-8"),
            "KiCad_DRC_Report.txt",
            "text/plain",
            key="download_original_drc"
        )

    except UnicodeDecodeError:
        st.error(
            "Could not read this report as UTF-8 text. "
            "Export a plain-text report from KiCad and try again."
        )

st.divider()


# ============================================================
# 5. PCB FILE UPLOAD
# ============================================================

st.header("2. PCB Geometry and Track Analysis")

uploaded_file = st.file_uploader(
    "Upload a KiCad PCB file (.kicad_pcb)",
    type=["kicad_pcb"],
    key="pcb_file_upload"
)

if uploaded_file is None:
    st.markdown("""
    ### How to use this dashboard

    1. Upload a KiCad DRC report to view actual DRC results.
    2. Upload a KiCad PCB file to inspect geometry.
    3. Review components, track segments, and vias.
    4. Adjust the track-width screening threshold.
    5. Review the preliminary risk score and routing-density analysis.
    6. Download the results and combined report.
    """)
    st.stop()


# ============================================================
# 6. PARSE PCB AND RUN ANALYSIS
# ============================================================

try:
    pcb_text = uploaded_file.getvalue().decode("utf-8")

    if "(kicad_pcb" not in pcb_text[:5000]:
        st.warning(
            "The file does not appear to contain a standard KiCad PCB "
            "declaration near the beginning. Results may be incomplete."
        )

    components, tracks, via_count = parse_pcb(pcb_text)

    if not tracks:
        st.error(
            "No track segments were extracted. The board may have no "
            "routed tracks, or the parser may not support its format."
        )
        st.stop()

    df = pd.DataFrame(tracks)

    df["Review Flag"] = df["Width (mm)"].apply(
        lambda width: (
            "Unknown width"
            if pd.isna(width)
            else "Review width"
            if width < min_width
            else "No width flag"
        )
    )

    flagged_count = int(
        (df["Review Flag"] != "No width flag").sum()
    )

    risk = calculate_review_risk(
        df,
        min_width,
        drc_results
    )

    st.success(
        "PCB file parsed. Review the extracted data below."
    )


    # ========================================================
    # 7. PRELIMINARY RISK SCORE
    # ========================================================

    st.header("3. Preliminary Risk Score and Reasons")

    st.caption(
        "This transparent rule-based score uses width screening and, "
        "when supplied, parsed DRC results. It is not an AI/ML prediction "
        "and does not assess EMI/SI or thermal behaviour."
    )

    a, b, c = st.columns(3)

    a.metric(
        "Review-priority score",
        f"{risk['score']}/100"
    )

    b.metric(
        "Review level",
        risk["level"]
    )

    c.metric(
        "Evidence coverage",
        risk["coverage"]
    )

    if risk["level"] == "High":
        st.error(
            "High review priority — investigate the listed issues "
            "before fabrication."
        )
    elif risk["level"] == "Medium":
        st.warning(
            "Medium review priority — inspect the listed issues "
            "against design rules."
        )
    else:
        st.info(
            "Low review priority based on the checks available. "
            "This does not mean the PCB is free of all design risks."
        )

    st.markdown("**Reasons**")

    for reason in risk["reasons"]:
        st.markdown(f"- {reason}")

    st.markdown("**Recommended next actions**")

    for action in risk["recommendations"]:
        st.markdown(f"- {action}")

    a, b, c, d = st.columns(4)

    a.metric("Components found", len(components))
    b.metric("Track segments", len(df))
    c.metric("Vias found", via_count)
    d.metric("Tracks flagged", flagged_count)

    st.caption(
        f"Screening threshold: {min_width:.2f} mm. "
        "A flag means a track needs review against actual design rules; "
        "it does not prove the track is defective."
    )


    # ========================================================
    # 8. TRACK REVIEW TABLE
    # ========================================================

    st.subheader("Track Review")

    filter_choice = st.selectbox(
        "Filter tracks",
        [
            "All tracks",
            "Only tracks flagged for review",
            "Tracks without width flags"
        ]
    )

    if filter_choice == "Only tracks flagged for review":
        shown_df = df[
            df["Review Flag"] != "No width flag"
        ]
    elif filter_choice == "Tracks without width flags":
        shown_df = df[
            df["Review Flag"] == "No width flag"
        ]
    else:
        shown_df = df

    display_columns = [
        "Track",
        "Width (mm)",
        "Length (mm)",
        "Layer",
        "Review Flag"
    ]

    st.dataframe(
        shown_df[display_columns],
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Download track results as CSV",
        df[display_columns].to_csv(index=False).encode("utf-8"),
        "pcb_track_review.csv",
        "text/csv"
    )


    # ========================================================
    # 9. TRACK VISUALIZATION
    # ========================================================

    st.subheader("Extracted Track Visualization")

    fig, ax = plt.subplots(figsize=(10, 6))

    for track in tracks:
        ax.plot(
            [track["X1"], track["X2"]],
            [track["Y1"], track["Y2"]],
            linewidth=1.5
        )

    ax.set_xlabel("X coordinate (mm)")
    ax.set_ylabel("Y coordinate (mm)")
    ax.set_title("Extracted PCB Track Segments")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    st.pyplot(fig)

    image_buffer = BytesIO()
    fig.savefig(
        image_buffer,
        format="png",
        dpi=150
    )
    plt.close(fig)

    st.download_button(
        "Download track visualization",
        image_buffer.getvalue(),
        "pcb_track_visualization.png",
        "image/png"
    )


    # ========================================================
    # 10. COMPONENT REFERENCES
    # ========================================================

    st.subheader("Component References")

    if components:
        st.write(", ".join(components))
    else:
        st.warning(
            "No component references were extracted. "
            "This may indicate a parser limitation."
        )


    # ========================================================
    # 11. ANALYSIS SUMMARY
    # ========================================================

    st.subheader("Analysis Summary")

    summary = pd.DataFrame({
        "Metric": [
            "PCB file",
            "Components found",
            "Track segments",
            "Vias found",
            "Width screening threshold (mm)",
            "Tracks flagged for review",
            "Preliminary review-priority score (/100)",
            "Preliminary review level",
            "Evidence coverage"
        ],
        "Result": [
            uploaded_file.name,
            len(components),
            len(df),
            via_count,
            f"{min_width:.2f}",
            flagged_count,
            risk["score"],
            risk["level"],
            risk["coverage"]
        ]
    })

    st.dataframe(
        summary,
        use_container_width=True,
        hide_index=True
    )


    # ========================================================
    # 12. ROUTING DENSITY ANALYSIS
    # ========================================================

    st.header("4. Routing Density Analysis")

    st.caption(
        "This heatmap estimates routing density from track-segment "
        "occupancy in grid cells. It does not calculate exact copper area "
        "or prove congestion, clearance problems, or electrical/thermal issues."
    )

    grid_divisions = st.slider(
        "Routing density grid divisions per side",
        min_value=5,
        max_value=20,
        value=10,
        step=1,
        help=(
            "More divisions show smaller regions; fewer divisions "
            "give a broader overview."
        )
    )

    (
        grid_df,
        layer_df,
        hotspot_df,
        density_counts,
        board_bounds
    ) = calculate_routing_density(
        tracks,
        grid_divisions
    )

    if density_counts:
        x_min, x_max, y_min, y_max = board_bounds

        heat_fig, heat_ax = plt.subplots(figsize=(10, 6))

        heat_image = heat_ax.imshow(
            density_counts,
            origin="lower",
            extent=[x_min, x_max, y_min, y_max],
            aspect="auto",
            interpolation="nearest"
        )

        heat_ax.set_xlabel("X coordinate (mm)")
        heat_ax.set_ylabel("Y coordinate (mm)")
        heat_ax.set_title(
            "Routing Density Proxy — Track Segment Occupancy"
        )

        heat_fig.colorbar(
            heat_image,
            ax=heat_ax,
            label="Distinct segments crossing cell"
        )

        heat_fig.tight_layout()
        st.pyplot(heat_fig)

        heat_buffer = BytesIO()

        heat_fig.savefig(
            heat_buffer,
            format="png",
            dpi=150
        )

        plt.close(heat_fig)

        st.download_button(
            "Download routing density heatmap",
            heat_buffer.getvalue(),
            "pcb_routing_density_heatmap.png",
            "image/png",
            key="download_density_heatmap"
        )

        density_col1, density_col2 = st.columns(2)

        density_col1.metric(
            "Grid cells",
            len(grid_df)
        )

        density_col2.metric(
            "Higher-density cells flagged",
            len(hotspot_df)
        )

        st.subheader("Layer-wise Track Statistics")

        st.dataframe(
            layer_df,
            use_container_width=True,
            hide_index=True
        )

        st.download_button(
            "Download layer statistics as CSV",
            layer_df.to_csv(index=False).encode("utf-8"),
            "pcb_layer_statistics.csv",
            "text/csv",
            key="download_layer_stats"
        )

        st.subheader("Potential Higher-Density Regions")

        if not hotspot_df.empty:
            st.warning(
                f"{len(hotspot_df)} grid cell(s) have relatively high "
                "segment occupancy at the selected grid resolution. "
                "Inspect these areas in KiCad."
            )

            st.dataframe(
                hotspot_df,
                use_container_width=True,
                hide_index=True
            )

            st.download_button(
                "Download density regions as CSV",
                hotspot_df.to_csv(index=False).encode("utf-8"),
                "pcb_routing_density_regions.csv",
                "text/csv",
                key="download_density_regions"
            )

            density_recommendations = [
                "Inspect the flagged coordinates in KiCad's board editor.",
                "Run KiCad DRC to check clearances and other configured board rules.",
                (
                    "For high-current or high-speed nets, separately verify "
                    "width, return paths, impedance, thermal rise, and signal "
                    "integrity as applicable."
                )
            ]

        else:
            st.info(
                "No relatively higher-density cells were identified at this "
                "grid resolution. This does not prove that routing congestion "
                "is absent."
            )

            density_recommendations = [
                (
                    "If congestion is suspected, change the grid resolution "
                    "and inspect the board in KiCad."
                ),
                (
                    "Run KiCad DRC and perform project-specific electrical "
                    "and thermal checks as needed."
                )
            ]

        st.markdown("**Routing density recommendations**")

        for recommendation in density_recommendations:
            st.markdown(f"- {recommendation}")

        st.download_button(
            "Download all grid-cell density data as CSV",
            grid_df.to_csv(index=False).encode("utf-8"),
            "pcb_routing_density_grid.csv",
            "text/csv",
            key="download_density_grid"
        )

    else:
        density_recommendations = [
            "No track geometry was available for routing density analysis."
        ]

        st.info(
            "Routing density could not be calculated because "
            "track geometry was unavailable."
        )


    # ========================================================
    # 13. PREPARE DRC SECTION FOR COMBINED HTML REPORT
    # ========================================================

    if drc_results is None:
        drc_html_section = (
            "<h2>KiCad DRC Results</h2>"
            "<p>No DRC report was uploaded during this session.</p>"
        )

    else:
        total_drc = drc_results["Total violations"]

        drc_summary = pd.DataFrame({
            "Metric": [
                "Source DRC report",
                "Total violations",
                "Errors identified",
                "Warnings identified",
                "Unconnected pads",
                "Footprint errors"
            ],
            "Result": [
                drc_file.name,
                (
                    total_drc
                    if total_drc is not None
                    else "Not identified"
                ),
                drc_results["Errors"],
                drc_results["Warnings"],
                (
                    drc_results["Unconnected pads"]
                    if drc_results["Unconnected pads"] is not None
                    else "Not reported"
                ),
                (
                    drc_results["Footprint errors"]
                    if drc_results["Footprint errors"] is not None
                    else "Not reported"
                )
            ]
        })

        drc_html_section = (
            "<h2>KiCad DRC Results</h2>"
            + drc_summary.to_html(index=False, escape=True)
        )

        if drc_results["Violations"]:
            drc_html_section += (
                "<h3>Individual DRC Violations</h3>"
                + pd.DataFrame(
                    drc_results["Violations"]
                ).to_html(index=False, escape=True)
            )

        drc_html_section += (
            "<h3>Original DRC Report</h3><pre>"
            + html.escape(drc_text or "")
            + "</pre>"
        )


    # ========================================================
    # 14. PREPARE ROUTING DENSITY REPORT SECTION
    # ========================================================

    density_layer_html = (
        layer_df.to_html(index=False, escape=True)
        if not layer_df.empty
        else "<p>No layer statistics available.</p>"
    )

    density_hotspot_html = (
        hotspot_df.to_html(index=False, escape=True)
        if not hotspot_df.empty
        else (
            "<p>No relative high-density cells were flagged at "
            "the selected grid resolution.</p>"
        )
    )

    density_html_section = (
        "<h2>Routing Density Analysis</h2>"
        "<p>Grid-cell segment occupancy is a geometry-based density proxy. "
        "It is not exact copper-area analysis and does not prove actual congestion.</p>"
        f"<p><strong>Grid resolution:</strong> "
        f"{grid_divisions} × {grid_divisions}</p>"
        "<h3>Layer-wise Track Statistics</h3>"
        + density_layer_html
        + "<h3>Potential Higher-Density Regions</h3>"
        + density_hotspot_html
        + "<h3>Recommendations</h3><ul>"
        + "".join(
            f"<li>{html.escape(item)}</li>"
            for item in density_recommendations
        )
        + "</ul>"
    )


    # ========================================================
    # 15. COMBINED HTML REPORT
    # ========================================================

    reasons_html = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in risk["reasons"]
    )

    actions_html = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in risk["recommendations"]
    )

    report_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Combined PCB Analysis Report</title>
<style>
body {{
    font-family: Arial, sans-serif;
    margin: 30px;
    line-height: 1.5;
    color: #222;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 24px;
}}
th, td {{
    border: 1px solid #ccc;
    padding: 8px;
    text-align: left;
    overflow-wrap: anywhere;
}}
th {{
    background: #eaf2f8;
}}
h1, h2 {{
    color: #17365d;
}}
pre {{
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: #f4f4f4;
    padding: 12px;
}}
</style>
</head>
<body>

<h1>Cloud-Based PCB Design Assistant</h1>

<p><strong>PCB file:</strong>
{html.escape(uploaded_file.name)}</p>

<p><strong>Generated:</strong>
{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

<h2>PCB Geometry Summary</h2>
{summary.to_html(index=False, escape=True)}

<h2>Preliminary Risk Score and Reasons</h2>

<p><strong>Review-priority score:</strong>
{risk['score']}/100</p>

<p><strong>Review level:</strong>
{html.escape(risk['level'])}</p>

<p><strong>Evidence coverage:</strong>
{html.escape(risk['coverage'])}</p>

<h3>Reasons</h3>
<ul>{reasons_html}</ul>

<h3>Recommended next actions</h3>
<ul>{actions_html}</ul>

{density_html_section}

<h2>Track Details</h2>
{df[display_columns].to_html(index=False, escape=True)}

<h2>Component References</h2>
<p>{
    html.escape(", ".join(components))
    if components
    else "None extracted"
}</p>

{drc_html_section}

<h2>Limitations</h2>

<p>
Track-width flags are screening results based on the selected threshold
and do not prove a design-rule violation. The review-priority score is
a heuristic based only on the checks listed in this report; it is not
an AI/ML prediction. KiCad DRC results are imported from a separately
generated report. This website does not execute the KiCad DRC engine
or certify manufacturing, electrical safety, EMI, thermal performance,
component placement, routing quality, or signal integrity. Routing
density is a grid-based segment-occupancy estimate and must not be
treated as proof of congestion or as a substitute for KiCad DRC and
engineering analysis.
</p>

</body>
</html>
"""

    st.download_button(
        "Download Combined PCB and DRC HTML Report",
        report_html.encode("utf-8"),
        "Combined_PCB_DRC_Report.html",
        "text/html"
    )


except UnicodeDecodeError:
    st.error("Could not read this PCB file as UTF-8 text.")

except Exception as error:
    st.error("Unable to analyze this PCB file.")
    st.exception(error)
