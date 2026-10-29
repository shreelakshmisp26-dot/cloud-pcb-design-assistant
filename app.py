
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
# 1. PCB FILE PARSING
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
    """Extract components, track geometry and via count."""

    components = sorted(set(re.findall(
        r'\(property\s+"Reference"\s+"([^"\n]+)',
        text
    )))

    components = [
        ref for ref in components
        if ref and ref != "REF**"
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
            "Y2": y2
        })

    via_count = len(extract_blocks(text, "via"))

    return components, tracks, via_count


# ============================================================
# 2. IMPROVED KICAD DRC REPORT PARSER
# ============================================================

def parse_drc_report(report_text):
    """
    Parse KiCad text-format DRC reports.

    Supports:
    - Rule: ...; error
    - Rule: ...; warning
    - Local override; error
    - Local override; warning

    KiCad may list unconnected items separately from
    the main DRC violation count.
    """

    result = {
        "Total violations": None,
        "Errors": 0,
        "Warnings": 0,
        "Unconnected pads": None,
        "Footprint errors": None,
        "Violations": []
    }

    count_patterns = {
        "Total violations":
            r"\*\*\s*Found\s+(\d+)\s+DRC violations?\s*\*",
        "Unconnected pads":
            r"\*\*\s*Found\s+(\d+)\s+unconnected pads?\s*\*",
        "Footprint errors":
            r"\*\*\s*Found\s+(\d+)\s+Footprint errors?\s*\*"
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

        violation_type = match.group(1).strip()
        description = match.group(2).strip().splitlines()[0]

        # Read the severity from the rule/override line,
        # rather than searching the violation description.
        severity_match = re.search(
            r"^\s*(?:Rule:[^\n;]*;\s*|Local override;\s*)"
            r"(error|warning)\b",
            block,
            flags=re.IGNORECASE | re.MULTILINE
        )

        severity = (
            severity_match.group(1).capitalize()
            if severity_match else "Unspecified"
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
            "Type": violation_type,
            "Description": description,
            "Severity": severity,
            "Details": " | ".join(details)
        })

    return result


# ============================================================
# 3. MANUAL DRC DIAGNOSIS AND FIX RECOMMENDATIONS
# ============================================================

def explain_drc_violation(violation):
    """
    Explain violations and suggest manual KiCad checks.
    No automatic PCB modifications are performed.
    """

    violation_type = str(
        violation.get("Type", "")
    ).lower().replace("-", "_").replace(" ", "_")

    description = str(violation.get("Description", ""))
    details = str(violation.get("Details", ""))
    severity = str(violation.get("Severity", "Unspecified"))

    guides = [
        (
            ("shorting_items", "short_circuit", "shorting"),
            "Two electrical nets that should be separate may be connected.",
            "Copper from different nets may overlap, a track may touch "
            "the wrong pad, or an object may have the wrong net assignment.",
            "1. Open the PCB in KiCad PCB Editor.\n"
            "2. Open Inspect > Design Rules Checker.\n"
            "3. Select the shorting violation to locate the objects.\n"
            "4. Check the track and pad net names.\n"
            "5. Remove or reroute the unintended connection.\n"
            "6. Run DRC again and confirm the short is gone."
        ),
        (
            ("unconnected_items", "unconnected", "missing_connection"),
            "A required electrical connection may be incomplete.",
            "A track may have been deleted, a pad may not be connected, "
            "or a routing gap may remain.",
            "1. Open the PCB in KiCad.\n"
            "2. Run Design Rules Checker.\n"
            "3. Select the unconnected-items violation.\n"
            "4. Identify the highlighted pads and their net.\n"
            "5. Compare the intended connection with the schematic.\n"
            "6. Route the missing connection if required.\n"
            "7. Run DRC again."
        ),
        (
            ("copper_edge_clearance", "board_edge_clearance"),
            "Copper is too close to the PCB board edge.",
            "A track, pad, or copper shape may violate the configured "
            "board-edge clearance.",
            "1. Open the PCB in KiCad.\n"
            "2. Select the board-edge clearance violation in DRC.\n"
            "3. Inspect the copper and Edge.Cuts outline.\n"
            "4. Check the minimum edge-clearance rule.\n"
            "5. Move or reroute the copper if appropriate.\n"
            "6. Run DRC again."
        ),
        (
            ("solder_mask_bridge",),
            "Solder-mask openings may leave insufficient mask separation.",
            "Mask openings around nearby copper features may overlap "
            "or violate the configured minimum solder-mask width.",
            "1. Open the PCB in KiCad.\n"
            "2. Locate the affected pads or tracks using DRC.\n"
            "3. Inspect the F.Mask or B.Mask layer.\n"
            "4. Check pad dimensions and mask expansion settings.\n"
            "5. Correct the geometry only if appropriate for the component "
            "and manufacturer.\n"
            "6. Run DRC again."
        ),
        (
            ("lib_footprint_mismatch",),
            "The PCB footprint differs from its library copy.",
            "The footprint may have been edited, or the installed "
            "library version may differ from the project.",
            "1. Locate the component named in the DRC report.\n"
            "2. Open its footprint properties.\n"
            "3. Compare it with the intended library footprint.\n"
            "4. Verify pad numbering, dimensions, and component compatibility.\n"
            "5. Update the footprint only if the library version is correct.\n"
            "6. Run DRC again."
        ),
        (
            ("lib_footprint_issues", "footprint_not_found"),
            "KiCad cannot locate or validate the referenced footprint.",
            "The library may be missing, renamed, or incorrectly configured.",
            "1. Identify the footprint mentioned in DRC.\n"
            "2. Check the footprint library table in KiCad.\n"
            "3. Verify the library name and installation path.\n"
            "4. Restore the correct library or choose a verified footprint.\n"
            "5. Confirm the footprint matches the actual component.\n"
            "6. Run DRC again."
        ),
        (
            ("silk_overlap", "silkscreen"),
            "Silkscreen graphics or text may overlap other objects.",
            "Reference text or component outlines may overlap pads, "
            "other labels, or silkscreen features.",
            "1. Select the silkscreen violation in DRC.\n"
            "2. Enable the F.Silkscreen or B.Silkscreen layer.\n"
            "3. Locate the affected text or graphic.\n"
            "4. Move or resize it without reducing readability.\n"
            "5. Run DRC again."
        ),
        (
            ("clearance",),
            "Objects may be closer than the configured minimum clearance.",
            "Copper objects may be too close together.",
            "1. Select the clearance violation in KiCad DRC.\n"
            "2. Locate the affected objects.\n"
            "3. Check their net assignments and configured clearance.\n"
            "4. Move or reroute the affected copper if needed.\n"
            "5. Run DRC again."
        ),
        (
            ("track_width", "width"),
            "A track may violate its configured minimum width.",
            "The track width may be smaller than the design-rule limit.",
            "1. Locate the track reported by KiCad.\n"
            "2. Check its width and net class.\n"
            "3. Review the actual current and fabrication requirements.\n"
            "4. Change the width if the design requires it.\n"
            "5. Run DRC again."
        ),
        (
            ("via",),
            "A via-related design rule may be violated.",
            "The via size, location, connectivity, or clearance "
            "may not satisfy the configured rules.",
            "1. Locate the via reported by KiCad.\n"
            "2. Inspect its net, diameter, drill size, and layers.\n"
            "3. Compare them with the board rules.\n"
            "4. Correct the via only if necessary.\n"
            "5. Run DRC again."
        ),
        (
            ("annular_ring", "annular_width"),
            "A pad or via may have insufficient annular ring.",
            "The difference between copper diameter and drill size "
            "may be too small.",
            "1. Locate the affected pad or via.\n"
            "2. Inspect its drill and copper dimensions.\n"
            "3. Check the board and manufacturer requirements.\n"
            "4. Correct the dimensions if necessary.\n"
            "5. Run DRC again."
        ),
        (
            ("hole", "drill"),
            "A drilled hole may violate a configured design rule.",
            "The hole size or clearance to another object may "
            "be insufficient.",
            "1. Locate the affected hole in KiCad.\n"
            "2. Inspect nearby copper, pads, and the board edge.\n"
            "3. Check drill size and required clearances.\n"
            "4. Correct the layout if necessary.\n"
            "5. Run DRC again."
        ),
        (
            ("footprint", "courtyard"),
            "A footprint or component placement issue may exist.",
            "Footprint dimensions, courtyard spacing, or placement "
            "may be inconsistent with the design.",
            "1. Locate the affected footprint.\n"
            "2. Inspect pads, orientation, and courtyard.\n"
            "3. Compare it with the actual component dimensions.\n"
            "4. Correct the placement or footprint if required.\n"
            "5. Run DRC again."
        )
    ]

    meaning = (
        "This violation requires further inspection in KiCad."
    )
    cause = (
        "The exact cause depends on the PCB design and its rules."
    )
    steps = (
        "1. Open the PCB in KiCad.\n"
        "2. Run Design Rules Checker.\n"
        "3. Select this violation and inspect the reported coordinates.\n"
        "4. Review the applicable design rule.\n"
        "5. Make an appropriate manual correction if needed.\n"
        "6. Run DRC again."
    )

    for keywords, guide_meaning, guide_cause, guide_steps in guides:
        if any(keyword in violation_type for keyword in keywords):
            meaning = guide_meaning
            cause = guide_cause
            steps = guide_steps
            break

    if severity == "Error":
        priority = "Higher — reported as an error"
    elif severity == "Warning":
        priority = "Review — reported as a warning"
    else:
        priority = "Unclassified — verify in KiCad"

    return {
        "Type": violation.get("Type", "Unknown"),
        "Severity": severity,
        "Priority guidance": priority,
        "Original description": description,
        "What it may mean": meaning,
        "Possible cause": cause,
        "Suggested KiCad checks": steps,
        "Reported details": details or "No location details parsed"
    }


# ============================================================
# 4. PRELIMINARY REVIEW-PRIORITY SCORE
# ============================================================

def calculate_review_risk(df, threshold, drc):
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
            f"({ratio * 100:.1f}%) need width review."
        )
        actions.append(
            "Compare flagged widths with the actual board rules, "
            "net classes and manufacturing requirements."
        )
    else:
        reasons.append(
            f"No track segments were below the selected "
            f"{threshold:.2f} mm screening threshold."
        )

    coverage = "Partial — no DRC report supplied"

    if drc is None:
        actions.append(
            "Run KiCad DRC and upload the report for additional review."
        )
    else:
        coverage = "Includes uploaded KiCad DRC report"

        errors = int(drc.get("Errors", 0) or 0)
        warnings = int(drc.get("Warnings", 0) or 0)
        unconnected = drc.get("Unconnected pads")
        footprint_errors = drc.get("Footprint errors")
        total = drc.get("Total violations")

        score += (
            min(40.0, errors * 20.0)
            + min(24.0, warnings * 8.0)
        )

        if total and not errors and not warnings:
            score += min(24.0, int(total) * 8.0)
            reasons.append(
                "Some violations could not be classified by severity."
            )
            actions.append(
                "Review the original KiCad report for completeness."
            )

        if errors:
            reasons.append(
                f"The DRC report identifies {errors} error(s)."
            )
            actions.append(
                "Resolve DRC errors and rerun KiCad DRC."
            )

        if warnings:
            reasons.append(
                f"The DRC report identifies {warnings} warning(s)."
            )
            actions.append(
                "Review DRC warnings and decide whether corrections "
                "are required."
            )

        if unconnected is not None:
            score += min(20.0, int(unconnected) * 10.0)

            if int(unconnected) > 0:
                reasons.append(
                    f"The report lists {unconnected} unconnected pad(s)."
                )
                actions.append(
                    "Inspect missing connections in KiCad."
                )

        if footprint_errors is not None:
            score += min(20.0, int(footprint_errors) * 10.0)

            if int(footprint_errors) > 0:
                reasons.append(
                    f"The report lists {footprint_errors} footprint error(s)."
                )
                actions.append(
                    "Inspect footprint errors in KiCad."
                )

        unspecified = sum(
            1 for v in drc.get("Violations", [])
            if v["Severity"] == "Unspecified"
        )

        if unspecified:
            reasons.append(
                f"{unspecified} report entry/entries have "
                "unclassified severity."
            )
            actions.append(
                "Check unclassified entries in the original report."
            )

    score = int(round(min(100.0, max(0.0, score))))

    level = (
        "Low" if score < 25
        else "Medium" if score < 60
        else "High"
    )

    if not actions:
        actions.append(
            "Continue reviewing the PCB against its design requirements."
        )

    return {
        "score": score,
        "level": level,
        "coverage": coverage,
        "reasons": reasons,
        "recommendations": actions
    }


# ============================================================
# 5. ROUTING DENSITY ANALYSIS
# ============================================================

def calculate_routing_density(tracks, divisions=10):
    """
    Count distinct track segments passing through grid cells.
    This is a density proxy, not exact copper-area analysis.
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
        step_size = max(0.01, min(cell_w, cell_h) / 2)

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
                "Segment occupancy": counts[row][col]
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

        hotspots = (
            hotspots
            .sort_values("Segment occupancy", ascending=False)
            .reset_index(drop=True)
        )
    else:
        hotspots = pd.DataFrame(columns=grid_df.columns)

    layer_df = (
        pd.DataFrame(tracks)
        .groupby("Layer", dropna=False)
        .agg(**{
            "Track segments": ("Track", "count"),
            "Total track length (mm)": ("Length (mm)", "sum"),
            "Average width (mm)": ("Width (mm)", "mean")
        })
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
# 6. PAGE HEADER AND SETTINGS
# ============================================================

st.title("🔌 Cloud-Based PCB Design Assistant")

st.write(
    "Analyze PCB geometry, review track widths, inspect KiCad "
    "DRC results, receive manual repair recommendations, "
    "and download a combined analysis report."
)

st.info(
    "Prototype notice: DRC results are imported from KiCad reports. "
    "This website does not independently run KiCad's DRC engine. "
    "Risk scores and density results are preliminary rule-based estimates."
)

with st.sidebar:
    st.header("Analysis Settings")

    min_width = st.number_input(
        "Flag tracks narrower than (mm)",
        min_value=0.01,
        max_value=10.0,
        value=0.20,
        step=0.05,
        help=(
            "User-selected screening threshold, not a universal "
            "PCB design-rule limit."
        )
    )

    st.caption(
        "Use the actual board requirements when known."
    )


# ============================================================
# 7. DRC REPORT UPLOAD
# ============================================================

st.header("1. KiCad DRC Report")

st.write(
    "Run Inspect → Design Rules Checker in KiCad, save the "
    "report, and upload the .rpt or .txt file."
)

drc_file = st.file_uploader(
    "Upload KiCad DRC report (.rpt or .txt)",
    type=["rpt", "txt"],
    key="drc_report_upload"
)

drc_results = None
drc_text = None
drc_explanations = []

if drc_file is not None:
    try:
        drc_text = drc_file.getvalue().decode("utf-8-sig")
        drc_results = parse_drc_report(drc_text)

        st.subheader("DRC Report Summary")
        st.caption(f"Report file: {drc_file.name}")

        a, b, c = st.columns(3)

        a.metric(
            "Total DRC violations",
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

        st.caption(
            "KiCad can report unconnected items separately from "
            "the main DRC violation total. Therefore, error and "
            "warning counts do not always sum to that total."
        )

        if drc_results["Total violations"] == 0:
            if not drc_results["Unconnected pads"]:
                st.success(
                    "The uploaded report indicates no DRC violations "
                    "or unconnected pads."
                )
            else:
                st.warning(
                    "No main DRC violations were reported, but "
                    "unconnected items are present."
                )
        elif drc_results["Total violations"] is not None:
            st.warning(
                f"KiCad reported {drc_results['Total violations']} "
                "DRC violation(s)."
            )
        else:
            st.info(
                "The DRC total could not be identified automatically."
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
        else:
            st.info(
                "No individual violation entries were recognized."
            )

        # ====================================================
        # 8. MANUAL DRC DIAGNOSIS AND FIX GUIDE
        # ====================================================

        st.subheader("DRC Error Diagnosis & Manual Fix Guide")

        st.caption(
            "The following guidance explains reported violations "
            "and suggests steps to check or correct them manually "
            "in KiCad. It does not automatically modify PCB files."
        )

        drc_explanations = [
            explain_drc_violation(item)
            for item in drc_results["Violations"]
        ]

        if drc_explanations:
            explanation_df = pd.DataFrame(drc_explanations)

            st.dataframe(
                explanation_df,
                use_container_width=True,
                hide_index=True
            )

            st.download_button(
                "Download DRC repair recommendations as CSV",
                explanation_df.to_csv(index=False).encode("utf-8"),
                "pcb_drc_repair_guide.csv",
                "text/csv",
                key="download_drc_explanations"
            )

            for index, item in enumerate(
                drc_explanations,
                start=1
            ):
                with st.expander(
                    f"Violation {index}: {item['Type']} "
                    f"— {item['Severity']}"
                ):
                    st.markdown("**Original violation:**")
                    st.write(item["Original description"])

                    st.markdown("**What it means:**")
                    st.write(item["What it may mean"])

                    st.markdown("**Possible cause:**")
                    st.write(item["Possible cause"])

                    st.markdown("**How to check/fix it in KiCad:**")
                    st.text(item["Suggested KiCad checks"])

                    st.markdown("**Reported location/details:**")
                    st.write(item["Reported details"])

                    st.info(
                        "After making a correction, rerun KiCad DRC "
                        "and upload the updated report to verify it."
                    )
        else:
            st.info(
                "No violations were recognized for explanation."
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
            "Could not read the report as UTF-8 text. "
            "Export a plain-text DRC report from KiCad."
        )

st.divider()


# ============================================================
# 9. PCB FILE UPLOAD
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

    1. Upload a KiCad DRC report.
    2. Review detected violations and manual repair guidance.
    3. Upload the matching KiCad PCB file.
    4. Review components, tracks and vias.
    5. Adjust the track-width screening threshold.
    6. View the routing-density heatmap.
    7. Download the combined analysis report.
    """)

    st.stop()


# ============================================================
# 10. PCB ANALYSIS
# ============================================================

try:
    pcb_text = uploaded_file.getvalue().decode("utf-8")

    if "(kicad_pcb" not in pcb_text[:5000]:
        st.warning(
            "The file does not appear to contain a standard "
            "KiCad PCB declaration near the beginning."
        )

    components, tracks, via_count = parse_pcb(pcb_text)

    if not tracks:
        st.error(
            "No track segments were extracted. The PCB may have "
            "no routed tracks, or the parser may not support its format."
        )
        st.stop()

    df = pd.DataFrame(tracks)

    df["Review Flag"] = df["Width (mm)"].apply(
        lambda width: (
            "Unknown width" if pd.isna(width)
            else "Review width" if width < min_width
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
        "PCB file parsed successfully."
    )

    # ========================================================
    # 11. PRELIMINARY RISK SCORE
    # ========================================================

    st.header("3. Preliminary Risk Score and Reasons")

    st.caption(
        "This is a rule-based review-priority score, not an "
        "AI prediction or manufacturing certification."
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
            "High review priority — investigate the reported issues."
        )
    elif risk["level"] == "Medium":
        st.warning(
            "Medium review priority — inspect the reported issues."
        )
    else:
        st.info(
            "Low review priority based on the available checks. "
            "This does not guarantee that the PCB is error-free."
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
        f"Track-width screening threshold: {min_width:.2f} mm. "
        "A width flag is not proof of a design-rule violation."
    )

    # ========================================================
    # 12. TRACK REVIEW AND CSV DOWNLOAD
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
    # 13. TRACK VISUALIZATION
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
    # 14. COMPONENT REFERENCES
    # ========================================================

    st.subheader("Component References")

    if components:
        st.write(", ".join(components))
    else:
        st.warning(
            "No component references were extracted."
        )

    # ========================================================
    # 15. ANALYSIS SUMMARY
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
    # 16. ROUTING DENSITY ANALYSIS
    # ========================================================

    st.header("4. Routing Density Analysis")

    st.caption(
        "This heatmap estimates routing density from track-segment "
        "occupancy in grid cells. It does not calculate exact copper "
        "area or prove congestion."
    )

    grid_divisions = st.slider(
        "Routing density grid divisions per side",
        min_value=5,
        max_value=20,
        value=10,
        step=1
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

    density_recommendations = []

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
                f"{len(hotspot_df)} grid cell(s) have relatively "
                "high segment occupancy."
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
                "Inspect the flagged coordinates in KiCad.",
                "Run KiCad DRC to check the configured board rules.",
                "Review high-current or high-speed nets separately "
                "for their electrical requirements."
            ]
        else:
            st.info(
                "No relatively higher-density cells were identified "
                "at this grid resolution."
            )

            density_recommendations = [
                "Try another grid resolution if congestion is suspected.",
                "Run KiCad DRC and perform project-specific checks."
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

    # ========================================================
    # 17. COMBINED HTML REPORT
    # ========================================================

    if drc_results is None:
        drc_html_section = (
            "<h2>KiCad DRC Results</h2>"
            "<p>No DRC report was uploaded.</p>"
        )
    else:
        drc_summary = pd.DataFrame({
            "Metric": [
                "Source DRC report",
                "Total DRC violations",
                "Errors identified",
                "Warnings identified",
                "Unconnected pads",
                "Footprint errors"
            ],
            "Result": [
                drc_file.name,
                (
                    drc_results["Total violations"]
                    if drc_results["Total violations"] is not None
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

        if drc_explanations:
            drc_html_section += (
                "<h3>DRC Error Diagnosis and Manual Fix Guide</h3>"
                "<p>Recommendations are rule-based suggestions. "
                "Verify all corrections using KiCad DRC.</p>"
                + pd.DataFrame(
                    drc_explanations
                ).to_html(index=False, escape=True)
            )

        drc_html_section += (
            "<h3>Original DRC Report</h3><pre>"
            + html.escape(drc_text or "")
            + "</pre>"
        )

    reasons_html = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in risk["reasons"]
    )

    actions_html = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in risk["recommendations"]
    )

    density_layer_html = (
        layer_df.to_html(index=False, escape=True)
        if not layer_df.empty
        else "<p>No layer statistics available.</p>"
    )

    density_hotspot_html = (
        hotspot_df.to_html(index=False, escape=True)
        if not hotspot_df.empty
        else "<p>No density hotspots were flagged.</p>"
    )

    density_html_section = (
        "<h2>Routing Density Analysis</h2>"
        "<p>Grid-cell segment occupancy is a geometry-based "
        "density proxy, not exact copper-area analysis.</p>"
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
<p><strong>PCB file:</strong> {html.escape(uploaded_file.name)}</p>
<p><strong>Generated:</strong>
{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

<h2>PCB Geometry Summary</h2>
{summary.to_html(index=False, escape=True)}

<h2>Preliminary Risk Score and Reasons</h2>
<p><strong>Review-priority score:</strong> {risk['score']}/100</p>
<p><strong>Review level:</strong> {html.escape(risk['level'])}</p>
<p><strong>Evidence coverage:</strong> {html.escape(risk['coverage'])}</p>

<h3>Reasons</h3>
<ul>{reasons_html}</ul>

<h3>Recommended Next Actions</h3>
<ul>{actions_html}</ul>

{density_html_section}

<h2>Track Details</h2>
{df[display_columns].to_html(index=False, escape=True)}

<h2>Component References</h2>
<p>{html.escape(', '.join(components)) if components else 'None extracted'}</p>

{drc_html_section}

<h2>Limitations</h2>
<p>
Track-width flags are screening results, not proof of design-rule
violations. The review-priority score is heuristic and not an AI/ML
prediction. KiCad DRC results are imported from a separately generated
report. This website does not execute the KiCad DRC engine or certify
manufacturing safety, electrical performance, thermal behaviour,
signal integrity, or routing quality. Routing density is estimated
using grid-based track-segment occupancy.
</p>

</body>
</html>"""

    st.download_button(
        "Download Combined PCB and DRC HTML Report",
        report_html.encode("utf-8"),
        "Combined_PCB_DRC_Report.html",
        "text/html"
    )

except UnicodeDecodeError:
    st.error(
        "Could not read this PCB file as UTF-8 text."
    )

except Exception as error:
    st.error(
        "Unable to analyze this PCB file."
    )
    st.exception(error)
