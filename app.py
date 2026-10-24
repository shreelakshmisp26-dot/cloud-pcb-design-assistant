
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


# ==================================================
# 1. HELPER FUNCTIONS
# ==================================================

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
    """Extract basic component references, track segments and vias."""

    components = re.findall(
        r'\(property\s+"Reference"\s+"([^"]+)"',
        text
    )

    components = sorted(set(
        ref for ref in components
        if ref and ref not in {"REF**", ""}
    ))

    tracks = []

    for block in extract_blocks(text, "segment"):
        start = re.search(
            r'\(start\s+([-+\d.eE]+)\s+([-+\d.eE]+)',
            block
        )
        end = re.search(
            r'\(end\s+([-+\d.eE]+)\s+([-+\d.eE]+)',
            block
        )
        width = re.search(
            r'\(width\s+([-+\d.eE]+)\)',
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
        width_mm = float(width.group(1)) if width else None

        tracks.append({
            "Track": len(tracks) + 1,
            "Width (mm)": width_mm,
            "Length (mm)": round(
                math.hypot(x2 - x1, y2 - y1), 3
            ),
            "Layer": layer.group(1) if layer else "Unknown",
            "X1": x1,
            "Y1": y1,
            "X2": x2,
            "Y2": y2
        })

    vias = extract_blocks(text, "via")
    return components, tracks, len(vias)


def parse_drc_report(report_text):
    """Parse KiCad text DRC report summary and violation details."""

    result = {
        "Total violations": None,
        "Errors": 0,
        "Warnings": 0,
        "Unconnected pads": None,
        "Footprint errors": None,
        "Violations": []
    }

    total_matches = re.findall(
        r"\*\*\s*Found\s+(\d+)\s+DRC violations?\s*\*\*",
        report_text,
        flags=re.IGNORECASE
    )

    if total_matches:
        result["Total violations"] = int(total_matches[-1])

    unconnected_matches = re.findall(
        r"\*\*\s*Found\s+(\d+)\s+unconnected pads?\s*\*\*",
        report_text,
        flags=re.IGNORECASE
    )

    if unconnected_matches:
        result["Unconnected pads"] = int(unconnected_matches[-1])

    footprint_matches = re.findall(
        r"\*\*\s*Found\s+(\d+)\s+Footprint errors?\s*\*\*",
        report_text,
        flags=re.IGNORECASE
    )

    if footprint_matches:
        result["Footprint errors"] = int(footprint_matches[-1])

    # KiCad text reports use entries such as:
    # [silk_overlap]: Silkscreen clearance
    #     Rule: board setup constraints silk; warning
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
            "Type": violation_type,
            "Description": description,
            "Severity": severity,
            "Details": " | ".join(details)
        })

    return result


# ==================================================
# 2. PAGE HEADER AND SETTINGS
# ==================================================

st.title("🔌 Cloud-Based PCB Design Assistant")

st.write(
    "Analyze basic PCB geometry, review track widths, and inspect "
    "actual DRC results exported from KiCad."
)

st.info(
    "This is a prototype. Track-width flags are screening results, "
    "not proof of design-rule violations. The website displays imported "
    "KiCad DRC reports but does not run the KiCad DRC engine itself."
)

with st.sidebar:
    st.header("Analysis Settings")

    min_width = st.number_input(
        "Flag tracks narrower than (mm)",
        min_value=0.01,
        max_value=10.0,
        value=0.20,
        step=0.05,
        help="A user-selected screening threshold, not a universal limit."
    )

    st.caption(
        "Use actual board requirements when known. "
        "The default is for demonstration only."
    )


# ==================================================
# 3. DRC REPORT UPLOAD
# ==================================================

st.header("1. Real KiCad DRC Report")

st.write(
    "In KiCad, open Inspect → Design Rules Checker, run DRC, "
    "and save the report. Upload the resulting .rpt or .txt file here."
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

        total = drc_results["Total violations"]
        errors = drc_results["Errors"]
        warnings = drc_results["Warnings"]

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Total violations",
            total if total is not None else "Not identified"
        )
        c2.metric("Errors identified", errors)
        c3.metric("Warnings identified", warnings)

        if total is not None and total == 0:
            st.success("The report indicates zero DRC violations.")
        elif total is not None:
            st.warning(
                f"KiCad reported {total} DRC violation(s). "
                "Review the details below."
            )
        else:
            st.info(
                "The total count could not be identified automatically. "
                "Check the original report below."
            )

        c4, c5 = st.columns(2)

        unconnected = drc_results["Unconnected pads"]
        footprint_errors = drc_results["Footprint errors"]

        c4.metric(
            "Unconnected pads",
            unconnected if unconnected is not None else "Not reported"
        )
        c5.metric(
            "Footprint errors",
            footprint_errors
            if footprint_errors is not None
            else "Not reported"
        )

        st.subheader("Individual DRC Violations")

        if drc_results["Violations"]:
            violation_df = pd.DataFrame(drc_results["Violations"])

            st.dataframe(
                violation_df,
                use_container_width=True,
                hide_index=True
            )

            st.download_button(
                "Download DRC violations as CSV",
                data=violation_df.to_csv(index=False).encode("utf-8"),
                file_name="pcb_drc_violations.csv",
                mime="text/csv",
                key="download_drc_csv"
            )
        elif total == 0:
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
            data=drc_text.encode("utf-8"),
            file_name="KiCad_DRC_Report.txt",
            mime="text/plain",
            key="download_original_drc"
        )

    except UnicodeDecodeError:
        st.error(
            "Could not read this report as UTF-8 text. "
            "Export a plain-text report from KiCad and try again."
        )

st.divider()


# ==================================================
# 4. PCB FILE UPLOAD
# ==================================================

st.header("2. PCB Geometry and Track Analysis")

uploaded_file = st.file_uploader(
    "Upload a KiCad PCB file (.kicad_pcb)",
    type=["kicad_pcb"],
    key="pcb_file_upload"
)

if uploaded_file is None:
    st.markdown(
        """
        ### How to use this dashboard

        1. Upload a KiCad DRC report to view actual DRC results.
        2. Upload a KiCad PCB file to inspect its geometry.
        3. Review components, track segments and vias.
        4. Adjust the track-width screening threshold.
        5. Filter the results and download reports.
        """
    )
    st.stop()


# ==================================================
# 5. PARSE PCB
# ==================================================

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

    st.success("PCB file parsed. Review the extracted data below.")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Components found", len(components))
    col2.metric("Track segments", len(df))
    col3.metric("Vias found", via_count)
    col4.metric("Tracks flagged", flagged_count)

    st.caption(
        f"Screening threshold: {min_width:.2f} mm. "
        "A flag means a track needs review against actual design rules; "
        "it does not prove that a track is defective."
    )


    # ==================================================
    # 6. TRACK REVIEW TABLE
    # ==================================================

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
        shown_df = df[df["Review Flag"] != "No width flag"]
    elif filter_choice == "Tracks without width flags":
        shown_df = df[df["Review Flag"] == "No width flag"]
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
        data=df[display_columns].to_csv(index=False).encode("utf-8"),
        file_name="pcb_track_review.csv",
        mime="text/csv"
    )


    # ==================================================
    # 7. TRACK VISUALIZATION
    # ==================================================

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
    fig.savefig(image_buffer, format="png", dpi=150)
    plt.close(fig)

    st.download_button(
        "Download track visualization",
        data=image_buffer.getvalue(),
        file_name="pcb_track_visualization.png",
        mime="image/png"
    )


    # ==================================================
    # 8. COMPONENT REFERENCES
    # ==================================================

    st.subheader("Component References")

    if components:
        st.write(", ".join(components))
    else:
        st.warning(
            "No component references were extracted. "
            "This may indicate a parser limitation."
        )


    # ==================================================
    # 9. SUMMARY
    # ==================================================

    st.subheader("Analysis Summary")

    summary = pd.DataFrame({
        "Metric": [
            "PCB file",
            "Components found",
            "Track segments",
            "Vias found",
            "Width screening threshold (mm)",
            "Tracks flagged for review"
        ],
        "Result": [
            uploaded_file.name,
            len(components),
            len(df),
            via_count,
            f"{min_width:.2f}",
            flagged_count
        ]
    })

    st.dataframe(
        summary,
        use_container_width=True,
        hide_index=True
    )


    # ==================================================
    # 10. COMBINED HTML REPORT
    # ==================================================

    if drc_results is None:
        drc_html_section = """
        <h2>KiCad DRC Results</h2>
        <p>No DRC report was uploaded during this session.</p>
        """
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
                total_drc if total_drc is not None else "Not identified",
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
            violation_df = pd.DataFrame(drc_results["Violations"])
            drc_html_section += (
                "<h3>Individual DRC Violations</h3>"
                + violation_df.to_html(index=False, escape=True)
            )

        drc_html_section += (
            "<h3>Original DRC Report</h3><pre>"
            + html.escape(drc_text)
            + "</pre>"
        )

    report_html = f"""
    <!DOCTYPE html>
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

    <h2>Track Details</h2>
    {df[display_columns].to_html(index=False, escape=True)}

    <h2>Component References</h2>
    <p>{html.escape(", ".join(components)) if components else "None extracted"}</p>

    {drc_html_section}

    <h2>Limitations</h2>
    <p>
    Track-width flags are screening results based on the selected
    threshold and do not prove a design-rule violation. KiCad DRC
    results are imported from a separately generated report. This
    website does not execute the KiCad DRC engine or certify
    manufacturing, electrical safety, EMI, thermal performance,
    or signal integrity.
    </p>
    </body>
    </html>
    """

    st.download_button(
        "Download Combined PCB and DRC HTML Report",
        data=report_html.encode("utf-8"),
        file_name="Combined_PCB_DRC_Report.html",
        mime="text/html"
    )

except UnicodeDecodeError:
    st.error("Could not read this PCB file as UTF-8 text.")

except Exception as error:
    st.error("Unable to analyze this PCB file.")
    st.exception(error)
