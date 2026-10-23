
import re
import html
import math
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
    """Extract basic components, tracks, and vias."""
    component_refs = re.findall(
        r'\(property\s+"Reference"\s+"([^"]+)"',
        text
    )

    # Exclude generic non-component reference values.
    component_refs = [
        ref for ref in component_refs
        if ref and ref not in {"", "REF**"}
    ]

    segments = extract_blocks(text, "segment")
    vias = extract_blocks(text, "via")
    tracks = []

    for block in segments:
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
        length_mm = math.hypot(x2 - x1, y2 - y1)

        tracks.append({
            "Track": len(tracks) + 1,
            "Width (mm)": width_mm,
            "Length (mm)": round(length_mm, 3),
            "Layer": layer.group(1) if layer else "Unknown",
            "X1": x1,
            "Y1": y1,
            "X2": x2,
            "Y2": y2
        })

    return component_refs, tracks, len(vias)


st.title("🔌 Cloud-Based PCB Design Assistant")
st.write(
    "Upload a KiCad PCB file to inspect basic geometry, "
    "track widths, and preliminary review flags."
)

st.info(
    "This is a prototype screening tool, not a substitute for "
    "KiCad Design Rules Checker, signal-integrity analysis, "
    "EMI simulation, or thermal simulation."
)

uploaded_file = st.file_uploader(
    "Upload a KiCad PCB file",
    type=["kicad_pcb"]
)

if uploaded_file is not None:
    try:
        pcb_text = uploaded_file.getvalue().decode("utf-8")

        if "(kicad_pcb" not in pcb_text[:5000]:
            st.warning(
                "The file does not appear to begin with a standard "
                "KiCad PCB declaration. Results may be incomplete."
            )

        components, tracks, via_count = parse_pcb(pcb_text)

        if not tracks:
            st.error(
                "No track segments were parsed. Check that this is "
                "a valid KiCad PCB file."
            )
            st.stop()

        df = pd.DataFrame(tracks)

        # A demonstration threshold only, not a universal design rule.
        df["Review Flag"] = df["Width (mm)"].apply(
            lambda width: (
                "Unknown width" if pd.isna(width)
                else "Review width" if width < 0.20
                else "No width flag"
            )
        )

        st.success("PCB file parsed successfully.")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Components found", len(components))
        col2.metric("Track segments", len(df))
        col3.metric("Vias found", via_count)
        col4.metric(
            "Tracks flagged",
            int((df["Review Flag"] != "No width flag").sum())
        )

        st.subheader("1. Preliminary Track Review")
        st.caption(
            "Widths below 0.20 mm are flagged for review in this demo. "
            "Actual limits depend on fabrication rules and electrical needs."
        )

        st.dataframe(
            df[["Track", "Width (mm)", "Length (mm)", "Layer", "Review Flag"]],
            use_container_width=True,
            hide_index=True
        )

        st.subheader("2. PCB Track Visualization")

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

        st.subheader("3. Component References")
        if components:
            st.write(", ".join(components))
        else:
            st.write(
                "No component references were extracted. "
                "Check the PCB file and parser compatibility."
            )

        st.subheader("4. Download Analysis Report")

        summary = pd.DataFrame({
            "Metric": [
                "PCB file",
                "Components found",
                "Track segments",
                "Vias found",
                "Tracks flagged for width review"
            ],
            "Result": [
                uploaded_file.name,
                len(components),
                len(df),
                via_count,
                int((df["Review Flag"] != "No width flag").sum())
            ]
        })

        st.dataframe(summary, use_container_width=True, hide_index=True)

        report_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <title>PCB Analysis Report</title>
        <style>
        body {{ font-family: Arial, sans-serif; margin: 30px; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
        th {{ background: #eaf2f8; }}
        </style>
        </head>
        <body>
        <h1>Cloud-Based PCB Design Assistant</h1>
        <p>File: {html.escape(uploaded_file.name)}</p>
        <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        <h2>Summary</h2>
        {summary.to_html(index=False, escape=True)}
        <h2>Track Details</h2>
        {df[["Track", "Width (mm)", "Length (mm)", "Layer", "Review Flag"]].to_html(index=False, escape=True)}
        <h2>Limitations</h2>
        <p>This prototype performs basic PCB parsing and preliminary width
        screening. It does not establish EMI compliance, thermal safety,
        signal integrity, or manufacturing-rule compliance.</p>
        </body>
        </html>
        """

        st.download_button(
            "Download HTML analysis report",
            data=report_html.encode("utf-8"),
            file_name="PCB_Analysis_Report.html",
            mime="text/html"
        )

    except Exception as error:
        st.error("Unable to analyze this file.")
        st.exception(error)

else:
    st.markdown(
        """
        ### How to use the dashboard
        1. Upload a `.kicad_pcb` file.
        2. Review component and track statistics.
        3. Inspect the extracted track visualization.
        4. Download the analysis report.
        """
    )
