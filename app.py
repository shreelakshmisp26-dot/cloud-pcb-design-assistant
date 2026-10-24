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

    <h2>Preliminary Risk Score and Reasons</h2>
    <p><strong>Review-priority score:</strong> {risk_assessment["score"]}/100</p>
    <p><strong>Review level:</strong> {html.escape(risk_assessment["level"])}</p>
    <p><strong>Evidence coverage:</strong> {html.escape(risk_assessment["coverage"])}</p>
    <h3>Reasons</h3>
    <ul>
    {''.join(f"<li>{html.escape(reason)}</li>" for reason in risk_assessment["reasons"])}
    </ul>
    <h3>Recommended next actions</h3>
    <ul>
    {''.join(f"<li>{html.escape(item)}</li>" for item in risk_assessment["recommendations"])}
    </ul>

    <h2>Track Details</h2>
    {df[display_columns].to_html(index=False, escape=True)}

    <h2>Component References</h2>
    <p>{html.escape(", ".join(components)) if components else "None extracted"}</p>

    {drc_html_section}

    <h2>Limitations</h2>
    <p>
    Track-width flags are screening results based on the selected
    threshold and do not prove a design-rule violation. The review-priority
    score is a transparent heuristic based only on the checks listed in this
    report; it is not an AI/ML prediction. KiCad DRC results are imported from
    a separately generated report. This website does not execute the KiCad DRC
    engine or certify manufacturing, electrical safety, EMI, thermal performance,
    component placement, routing quality, or signal integrity.
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
