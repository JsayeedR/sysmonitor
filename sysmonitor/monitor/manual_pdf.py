"""
PDF renderer for the safe SysMonitor Operations Manual.
"""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .manual_content import (
    ARCHITECTURE_STEPS,
    MANUAL_INTRO,
    OPERATING_WORKFLOWS,
    PAGE_GUIDES,
    ROLE_GUIDE,
    SECURITY_RULES,
    STATUS_GLOSSARY,
    TROUBLESHOOTING,
)


def _txt(value):
    return escape(str(value or ""))


def build_manual_pdf(version):
    """Return PDF bytes for the current SysMonitor user manual."""
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=17 * mm,
        bottomMargin=17 * mm,
        title=f"SysMonitor Operations Manual v{version}",
        author="SysMonitor",
        subject="Safe user and administrator operations guide",
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ManualTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=27,
        textColor=colors.HexColor("#0f172a"),
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    version_style = ParagraphStyle(
        "Version",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=colors.HexColor("#2563eb"),
        alignment=TA_CENTER,
        spaceAfter=16,
    )
    h1 = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=10,
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=16,
        textColor=colors.HexColor("#1d4ed8"),
        spaceBefore=8,
        spaceAfter=5,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#334155"),
        spaceAfter=5,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#475569"),
    )
    bullet = ParagraphStyle(
        "Bullet",
        parent=body,
        leftIndent=10,
        firstLineIndent=-6,
        bulletIndent=3,
        spaceAfter=3,
    )
    tag = ParagraphStyle(
        "Tag",
        parent=small,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#475569"),
    )

    story = [
        Spacer(1, 18 * mm),
        Paragraph(_txt(MANUAL_INTRO["title"]), title),
        Paragraph(f"Version v{_txt(version)}", version_style),
        Paragraph(_txt(MANUAL_INTRO["subtitle"]), ParagraphStyle(
            "Lead", parent=body, fontSize=11, leading=16, alignment=TA_CENTER
        )),
        Spacer(1, 4 * mm),
        Paragraph(_txt(MANUAL_INTRO["scope"]), body),
        Spacer(1, 8 * mm),
        Paragraph("Security note", h2),
        Paragraph(
            "This manual intentionally contains no passwords, tokens, private "
            "addresses, recipient details, or other credentials.",
            body,
        ),
        PageBreak(),
        Paragraph("1. Roles and access", h1),
    ]

    role_data = [["Role", "Purpose", "Operating meaning"]]
    for item in ROLE_GUIDE:
        role_data.append([
            Paragraph(_txt(item["role"]), tag),
            Paragraph(_txt(item["summary"]), small),
            Paragraph(_txt(item["details"]), small),
        ])
    role_table = Table(role_data, colWidths=[25*mm, 48*mm, 95*mm], repeatRows=1)
    role_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e2e8f0")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.HexColor("#0f172a")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#cbd5e1")),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    story += [
        role_table,
        Spacer(1, 5 * mm),
        Paragraph(
            "Role permission is the maximum access ceiling. Individual Page "
            "Access settings may hide additional pages but do not grant higher privileges.",
            body,
        ),
        Paragraph("2. How SysMonitor works", h1),
    ]

    for number, heading, detail in ARCHITECTURE_STEPS:
        story.append(
            KeepTogether([
                Paragraph(f"{_txt(number)}. {_txt(heading)}", h2),
                Paragraph(_txt(detail), body),
            ])
        )

    story += [Paragraph("3. Status glossary", h1)]
    glossary_data = [["Status", "Meaning"]]
    for status, meaning in STATUS_GLOSSARY:
        glossary_data.append([
            Paragraph(_txt(status), tag),
            Paragraph(_txt(meaning), small),
        ])
    gt = Table(glossary_data, colWidths=[38*mm, 130*mm], repeatRows=1)
    gt.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e2e8f0")),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story += [gt, PageBreak(), Paragraph("4. Page-by-page guide", h1)]

    for idx, page in enumerate(PAGE_GUIDES, start=1):
        block = [
            Paragraph(
                f'{idx}. {_txt(page["title"])} '
                f'<font color="#64748b">({_txt(page["group"])})</font>',
                h2,
            ),
            Paragraph(f'<b>Available to:</b> {_txt(page["audience"])}', tag),
            Paragraph(_txt(page["purpose"]), body),
            Paragraph("<b>How it works</b>", body),
        ]
        for item in page["how"]:
            block.append(Paragraph("• " + _txt(item), bullet))
        block.append(Paragraph("<b>What it means</b>", body))
        for item in page["means"]:
            block.append(Paragraph("• " + _txt(item), bullet))
        block += [
            Paragraph("<b>Where to manage/change it</b>", body),
            Paragraph(_txt(page["manage"]), body),
            Spacer(1, 2 * mm),
        ]
        story.append(KeepTogether(block))

    story += [PageBreak(), Paragraph("5. Common operating workflows", h1)]
    for workflow in OPERATING_WORKFLOWS:
        story.append(Paragraph(_txt(workflow["title"]), h2))
        for n, step in enumerate(workflow["steps"], start=1):
            story.append(Paragraph(f"{n}. {_txt(step)}", bullet))

    story += [Paragraph("6. Security and safe use", h1)]
    for item in SECURITY_RULES:
        story.append(Paragraph("• " + _txt(item), bullet))

    story += [Paragraph("7. Troubleshooting quick reference", h1)]
    trouble_data = [["Symptom", "What to check"]]
    for symptom, advice in TROUBLESHOOTING:
        trouble_data.append([
            Paragraph(_txt(symptom), tag),
            Paragraph(_txt(advice), small),
        ])
    tt = Table(trouble_data, colWidths=[48*mm, 120*mm], repeatRows=1)
    tt.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e2e8f0")),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    story += [
        tt,
        Spacer(1, 6 * mm),
        Paragraph(
            f"End of SysMonitor Operations Manual v{_txt(version)}",
            ParagraphStyle("End", parent=small, alignment=TA_CENTER),
        ),
    ]

    def add_page_number(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(16 * mm, 9 * mm, f"SysMonitor Manual v{version}")
        canvas.drawRightString(
            A4[0] - 16 * mm,
            9 * mm,
            f"Page {doc_obj.page}",
        )
        canvas.restoreState()

    doc.build(
        story,
        onFirstPage=add_page_number,
        onLaterPages=add_page_number,
    )
    return stream.getvalue()
