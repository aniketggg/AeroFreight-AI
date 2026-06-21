"""
PDF invoice generation for the AeroFreight settlement/document package.

This is an AeroFreight demo/service invoice, not a customs-issued legal document.
"""

from __future__ import annotations

from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from shared_models import DocTemplates, EconData, RouteData, ShipmentRequest
from treasury_agent.pricing import FeeBreakdown


def _format_location(location: dict) -> str:
    city = str(location.get("city", "")).strip()
    state = str(location.get("state", "")).strip()
    country = str(location.get("country", "")).strip()
    parts = [part for part in (city, state, country) if part]
    return ", ".join(parts) if parts else "Unknown"


def _format_items(shipment: ShipmentRequest) -> str:
    if not shipment.items:
        return "None listed"
    return "; ".join(
        f"{item.name} x{item.quantity}" for item in shipment.items
    )


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
    """Write the invoice PDF to output_path and return that path."""
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Title"],
        fontSize=20,
        spaceAfter=4,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        spaceBefore=14,
        spaceAfter=6,
    )
    small_style = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.grey,
    )

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )
    story: list = []

    story.append(Paragraph("AeroFreight AI", title_style))
    story.append(
        Paragraph(
            "Route Optimization &amp; Compliance Document Service Invoice "
            "(demo/service invoice — not a customs-issued legal document)",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 6))

    meta_table = Table(
        [
            ["Invoice date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
            ["Session ID", session_id],
            ["Stripe payment reference", transaction_id],
        ],
        colWidths=[1.8 * inch, 4.2 * inch],
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
            ["Origin", _format_location(shipment.origin)],
            ["Destination", _format_location(shipment.destination)],
            ["Items", _format_items(shipment)],
            ["Total weight", f"{shipment.total_weight_kg:,.1f} kg"],
            ["Total volume", f"{shipment.total_volume_cbm:,.2f} CBM"],
            ["Declared value", f"${shipment.declared_value_usd:,.2f}"],
            ["Timeframe", shipment.timeframe],
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
        ["Route nodes", " -> ".join(route.optimal_route_nodes)],
        ["Countries visited", ", ".join(route.countries_visited)],
        ["Freight and toll cost", f"${route.freight_and_toll_cost_usd:,.2f}"],
        ["Entry tax", f"${econ.base_entry_tax_usd:,.2f}"],
        ["Total landed cost", f"${route.total_landed_cost_usd:,.2f}"],
    ]
    route_table = Table(route_rows, colWidths=[1.8 * inch, 4.2 * inch])
    route_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
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

    story.append(Paragraph("AeroFreight service fee (this invoice)", section_style))
    fee_rows = [
        ["Baseline (naive single-mode) cost", f"${fee.baseline_cost_usd:,.2f}"],
        ["Optimized cost (this route)", f"${fee.optimized_cost_usd:,.2f}"],
        ["Savings found by the agent", f"${fee.savings_usd:,.2f}"],
        ["Base fee (10% of savings)", f"${fee.base_fee_usd:,.2f}"],
    ]
    if fee.complexity_surcharge_usd:
        fee_rows.append(
            [
                "Multi-country documentation surcharge",
                f"${fee.complexity_surcharge_usd:,.2f}",
            ]
        )
    if fee.high_value_surcharge_usd:
        fee_rows.append(
            [
                "High-value handling surcharge",
                f"${fee.high_value_surcharge_usd:,.2f}",
            ]
        )
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
    for name in docs.required_form_names:
        story.append(Paragraph(f"&bull; {name}", styles["Normal"]))

    doc.build(story)
    return output_path
