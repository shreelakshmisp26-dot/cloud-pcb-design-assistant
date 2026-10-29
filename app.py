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
# 1. PCB PARSING
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
    """Extract component references, tracks, and vias."""

    components = sorted(
        set(
            re.findall(
                r'\(property\s+"Reference"\s+"([^"\n]+)',
                text
            )
        )
    )

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
            "Width (mm)": (
                float(width.group(1)) if width else None
            ),
            "Length (mm)": round(
                math.hypot(x2 - x1, y2 - y1),
                3
            ),
            "Layer": layer.group(1) if layer else "Unknown",
            "X1": x1,
            "Y1": y1,
            "X2": x2,
            "Y2": y2,
