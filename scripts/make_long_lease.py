"""Build a ~64-page fictional lease with two illegal clauses buried deep inside (stress test only).

    pip install reportlab && python scripts/make_long_lease.py OUTPUT.pdf
"""

import sys

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate

TOPICS = ["Use of Premises", "Occupancy Limits", "Guests", "Pets", "Smoking", "Parking", "Storage", "Trash and Recycling",
          "Noise and Quiet Hours", "Common Areas", "Laundry Facilities", "Appliances", "Window Coverings", "Alterations",
          "Painting and Decorating", "Locks and Keys", "Smoke and Carbon Monoxide Detectors", "Pest Control",
          "Mold and Moisture", "Lead-Based Paint Disclosure", "Renter's Insurance", "Assignment and Subletting",
          "Abandonment", "Holdover", "Move-Out Inspection", "Cleaning on Move-Out", "Notices", "Severability",
          "Entire Agreement", "Governing Law", "Bicycles", "Balconies and Patios", "Grills", "Satellite Dishes",
          "Waterbeds", "Snow and Ice", "Package Delivery", "Building Rules", "Emergency Contacts", "Fire Safety"]
PARA = ("Tenant shall comply with all reasonable rules adopted by Landlord concerning {t}, as provided to Tenant in writing "
        "from time to time, so long as such rules apply equally to all tenants of the building and do not reduce any right "
        "Tenant has under the laws of the State of Connecticut. Landlord will give Tenant reasonable written notice of any new "
        "or changed rule concerning {t} before it takes effect. Questions about {t} should be directed to the managing agent "
        "named in this Lease. Nothing in this section limits Landlord's obligation to maintain the Premises in a fit and "
        "habitable condition. ")


def main(out: str) -> None:
    st = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=st["Heading3"], fontSize=11)
    b = ParagraphStyle("b", parent=st["BodyText"], fontSize=10, leading=13)
    story = [Paragraph("RESIDENTIAL LEASE AGREEMENT (FICTIONAL TEST DOCUMENT)", st["Heading1"]),
             Paragraph('This lease is between Long Form Properties LLC ("Landlord") and Morgan Example ("Tenant"). '
                       "Monthly rent is $2,000, due on the first day of each month.", b)]
    n = 1
    for rep in range(5):
        for t in TOPICS:
            story.append(Paragraph(f"{n}. {t}" + (f" (Part {rep + 1})" if rep else ""), h))
            story.append(Paragraph(PARA.format(t=t.lower()) * 2, b))
            n += 1
            if rep == 3 and t == "Holdover":
                story.append(Paragraph(f"{n}. Security Deposit", h))
                story.append(Paragraph("Tenant shall pay a security deposit of $6,000 before move-in.", b))
                n += 1
            if rep == 4 and t == "Fire Safety":
                story.append(Paragraph(f"{n}. Late Charge", h))
                story.append(Paragraph("If rent is not received by the 2nd day of the month, Tenant shall pay a late fee of $150.", b))
                n += 1
    SimpleDocTemplate(out, pagesize=letter, leftMargin=inch, rightMargin=inch).build(story)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "long_lease.pdf")
