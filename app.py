
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


st.title("🔌 Cloud-Based PCB Design Assistant")
st.write(
    "Upload a KiCad PCB file to inspect basic board geometry "
    "and review track widths."
)

st.info(
    "Prototype only: this tool does not replace KiCad's Design "
    "Rules Checker, manufacturing checks, EMI simulation, "
    "thermal analysis, or signal-integrity analysis."
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
            "A user-selected screening threshold for this demo. "
            "It is not a universal manufacturing limit."
        )
    )

    st.caption(
        "Choose a threshold based on the actual board requirements "
        "when known. The default is for demonstration only."
    )

uploaded_file = st.file_uploader(
    "Upload a KiCad PCB file (.kicad_pcb)",
    type=["kicad_pcb"]
)

if uploaded_file is None:
    st.markdown(
        """
        ### How to use this dashboard
        1. Upload a KiCad PCB file.
        2. Review the extracted components, tracks and vias.
        3. Adjust the track-width screening threshold.
        4. Filter the results and download reports.
        """
    )
    st.stop()

try:
    pcb_text = uploaded_file.getvalue().decode("utf-8")

    if "(kicad_pcb" not in pcb_text[:5000]:
        st.warning(
            "The file does not appear to contain a standard KiCad "
            "PCB declaration near the beginning. Results may be incomplete."
        )

    components, tracks, via_count = parse_pcb(pcb_text)

    if not tracks:
        st.error(
            "No track segments were extracted. The file may contain "
            "no routed tracks or may need a more capable parser."
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
        "A flag means a track needs review against your actual design rules; "
        "it does not prove that a track is defective."
    )

    st.subheader("1. Track Review")

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
        "Track", "Width (mm)", "Length (mm)",
        "Layer", "Review Flag"
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

    st.subheader("2. Extracted Track Visualization")

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
        st.warning(
            "No component references were extracted. "
            "This may indicate a parser limitation."
        )

    st.subheader("4. Analysis Summary")

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

    report_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <title>PCB Analysis Report</title>
    <style>
    body {{ font-family: Arial, sans-serif; margin: 30px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
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
    {df[display_columns].to_html(index=False, escape=True)}
    <h2>Component References</h2>
    <p>{html.escape(", ".join(components)) if components else "None extracted"}</p>
    <h2>Limitations</h2>
    <p>This report contains basic parsed geometry and a configurable
    track-width screening check. It does not certify manufacturing,
    electrical safety, EMI compliance, thermal performance, or signal integrity.
    The selected width threshold is not a universal design rule.</p>
    </body>
    </html>
    """

    st.download_button(
        "Download HTML analysis report",
        data=report_html.encode("utf-8"),
        file_name="PCB_Analysis_Report.html",
        mime="text/html"
    )

except UnicodeDecodeError:
    st.error("Could not read this file as UTF-8 text.")
except Exception as error:
    st.error("Unable to analyze this PCB file.")
    st.exception(error)
