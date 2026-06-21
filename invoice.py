"""
PDF invoice generation for the AeroFreight settlement/document package.

Produces a clean, single-page invoice summarizing the route cost breakdown
and the service fee charged -- this is what gets uploaded to Google Drive
and linked back to the user once payment is verified.
"""

from __future__ import annotations

from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from models import DocTemplates, EconData, RouteData, ShipmentRequest
from pricing import FeeBreakdown


def generate_invoice_pdf(
    *,
    output_path: str,
    session_id: str,
    transaction_id: str,
    shipment: ShipmentRequest,
    econ: EconData,
    route: RouteData,
    docs: DocTemplates,
    fee: FeeBreakdown,
) -> str:
    """Writes the invoice PDF to output_path and returns that same path."""
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvoiceTitle", parent=styles["Title"], fontSize=20, spaceAfter=4
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6
    )
    small_style = ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, textColor=colors.grey)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )
    story = []

    story.append(Paragraph("AeroFreight AI", title_style))
    story.append(Paragraph("Route Optimization &amp; Compliance Document Service Invoice", styles["Normal"]))
    story.append(Spacer(1, 6))

    meta_table = Table(
        [
            ["Invoice date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
            ["Session ID", session_id],
            ["Transaction ID", transaction_id],
        ],
        colWidths=[1.6 * inch, 4.4 * inch],
    )
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(meta_table)

    story.append(Paragraph("Shipment", section_style))
    shipment_table = Table(
        [
            ["Origin", shipment.origin_country],
            ["Destination", shipment.destination_city],
            ["Weight", f"{shipment.weight_kg:,.1f} kg"],
            ["Volume", f"{shipment.volume_cbm:,.2f} CBM"],
            ["Declared value", f"${shipment.declared_value_usd:,.2f}"],
            ["Routing preference", shipment.timeframe_preference],
        ],
        colWidths=[1.8 * inch, 4.2 * inch],
    )
    shipment_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.whitesmoke),
            ]
        )
    )
    story.append(shipment_table)

    story.append(Paragraph("Route cost breakdown", section_style))
    route_rows = [
        ["Selected mode", route.selected_mode],
        ["Countries visited", ", ".join(route.countries_visited)],
        ["Freight cost", f"${route.freight_cost_usd:,.2f}"],
        ["Tolls / tariffs", f"${route.tolls_tariffs_usd:,.2f}"],
        ["Inland trucking", f"${route.inland_cost_usd:,.2f}"],
        ["Total route cost", f"${route.total_cost_usd:,.2f}"],
        ["Estimated entry tax*", f"${econ.entry_tax_usd:,.2f}"],
    ]
    route_table = Table(route_rows, colWidths=[1.8 * inch, 4.2 * inch])
    route_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("FONTNAME", (0, -2), (-1, -2), "Helvetica-Bold"),
                ("LINEABOVE", (0, -2), (-1, -2), 0.6, colors.black),
            ]
        )
    )
    story.append(route_table)
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "*Entry tax is an estimate to be remitted by your customs broker. "
            "AeroFreight AI does not collect, hold, or pay this amount.",
            small_style,
        )
    )

    story.append(Paragraph("Service fee (this invoice)", section_style))
    fee_rows = [
        ["Baseline (naive single-mode) cost", f"${fee.baseline_cost_usd:,.2f}"],
        ["Optimized cost (this route)", f"${fee.optimized_cost_usd:,.2f}"],
        ["Savings found by the agent", f"${fee.savings_usd:,.2f}"],
        ["Base fee (10% of savings)", f"${fee.base_fee_usd:,.2f}"],
    ]
    if fee.complexity_surcharge_usd:
        fee_rows.append(["Multi-country documentation surcharge", f"${fee.complexity_surcharge_usd:,.2f}"])
    if fee.high_value_surcharge_usd:
        fee_rows.append(["High-value handling surcharge", f"${fee.high_value_surcharge_usd:,.2f}"])
    fee_rows.append(["Total amount paid", f"${fee.total_fee_usd:,.2f}"])

    fee_table = Table(fee_rows, colWidths=[3.0 * inch, 3.0 * inch])
    fee_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
                ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
            ]
        )
    )
    story.append(fee_table)
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "This fee covers route optimization and compliance-document automation. "
            "It does not include or hold your shipment's value.",
            small_style,
        )
    )

    story.append(Paragraph("Included documents", section_style))
    for name in docs.doc_names:
        story.append(Paragraph(f"&bull; {name}", styles["Normal"]))

    doc.build(story)
    return output_path