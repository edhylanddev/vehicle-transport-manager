"""Build a fake Central Dispatch dispatch sheet PDF for tests.

The layout copies a real CD dispatch sheet: two-column sections (Shipper/Carrier,
Origin/Destination, Dates/Load Info), small grey labels with values underneath,
then a vehicle block (repeated per vehicle) and Additional Info. All data is made up.
"""
import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

W, H = letter
L1, L2, R1, R2 = 50, 190, 330, 470


def build(vehicles=None, payment="Carrier will receive $1,250 via Cash/Certified Funds upon Delivery.",
          total="$1,250", origin_name="--"):
    vehicles = vehicles or [
        {"ymm": "2019 Honda Accord", "vin": "1HGCV1F30KA000001", "type": "Car", "color": "Black", "lot": "--"},
        {"ymm": "2021 Ford F-150 XLT", "vin": "1FTEW1EP5MFA00002", "type": "Pickup", "color": "White",
         "lot": "LOT-77", "add": "Inoperable - needs winch"},
    ]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    def t(x, y, s, font="Helvetica", size=9.5, grey=False):
        c.setFont(font, size)
        c.setFillGray(0.45 if grey else 0)
        c.drawString(x, H - y, s)

    def head(x, y, s):
        t(x, y, s, "Helvetica-Bold", 11)

    def label(x, y, s):
        t(x, y, s, size=7.5, grey=True)

    y = 40
    t(L1, y, "Dispatch Sheet", "Helvetica-Bold", 14)
    y = 70
    head(L1, y, "Shipper Info"); head(R1, y, "Carrier Info")
    y += 16
    label(L1, y, "Shipper"); label(L2, y, "Contact Info"); label(R1, y, "Carrier"); label(R2, y, "Contact Info")
    y += 13
    t(L1, y, "Sunrise Auto Logistics LLC", "Helvetica-Bold"); t(L2, y, "Pat Example")
    t(R1, y, "Example Carrier Inc", "Helvetica-Bold"); t(R2, y, "Sam Driverly")
    y += 12
    t(L1, y, "100 Test Blvd Ste 5"); t(L2, y, "(555) 010-2000"); t(R1, y, "1 Main St"); t(R2, y, "(555) 010-3000")
    y += 12
    t(L1, y, "Orlando, FL 32801"); t(R1, y, "Fort Pierce, FL 34950")
    y += 18
    label(L1, y, "Special Number"); label(L2, y, "Dispatch Phone"); label(R1, y, "Special Number"); label(R2, y, "Phone 2")
    y += 13
    t(L1, y, "MC000111"); t(L2, y, "(555) 010-2001"); t(R1, y, "MC000222"); t(R2, y, "(555) 010-3001")
    y += 16
    label(R1, y, "Driver")
    y += 13
    t(R1, y, "--")

    y += 30
    head(L1, y, "Origin Info"); head(R1, y, "Destination Info")
    y += 16
    label(L1, y, "Origin"); label(L2, y, "Contact Info"); label(R1, y, "Destination"); label(R2, y, "Contact Info")
    y += 13
    t(L1, y, origin_name); t(L2, y, "Lot Desk"); t(R1, y, "--"); t(R2, y, "Jane Buyer")
    y += 12
    t(L1, y, "1552 Example View Drive"); t(L2, y, "(555) 010-4000"); t(R1, y, "80 Sample Ln"); t(R2, y, "(555) 010-5000")
    y += 12
    t(L1, y, "Dandridge, TN 37725"); t(R1, y, "Balsam Grove, NC 28708")
    y += 16
    label(L1, y, "Location Type"); label(L2, y, "Buyer Reference Number")
    label(R1, y, "Location Type"); label(R2, y, "Buyer Reference Number")
    y += 13
    t(L1, y, "Business"); t(L2, y, "--"); t(R1, y, "Residence"); t(R2, y, "--")

    y += 30
    head(L1, y, "Dates"); head(R1, y, "Load Info")
    y += 16
    label(L1, y, "Dispatch Date"); label(R1, y, "Load ID")
    y += 13
    t(L1, y, "11/12/2026"); t(R1, y, "301999888")
    y += 16
    label(L1, y, "Scheduled Pick-Up"); label(L2, y, "Scheduled Delivery"); label(R1, y, "Total Price")
    y += 13
    t(L1, y, "Exactly"); t(L2, y, "Estimated"); t(R1, y, total)
    y += 12
    t(L1, y, "11/14/2026"); t(L2, y, "11/16/2026")
    label(R1, y + 4, "Payment Terms")
    t(R1, y + 17, payment[:52])
    if len(payment) > 52:
        t(R1, y + 29, payment[52:])

    y += 50
    head(L1, y, f"Vehicle Info ({len(vehicles)} Total)")
    for v in vehicles:
        y += 16
        label(L1, y, "Vehicle Year/Make/Model"); label(L2, y, "VIN"); label(500, y, "Critical Notes")
        y += 13
        t(L1, y, v["ymm"]); t(L2, y, v["vin"]); t(500, y, "Wide Load" if v is vehicles[0] else "--")
        y += 16
        label(L1, y, "Vehicle Type"); label(L2, y, "Color"); label(R1, y, "License State/#"); label(420, y, "Lot #")
        y += 13
        t(L1, y, v["type"]); t(L2, y, v["color"]); t(R1, y, "--"); t(420, y, v["lot"])
        y += 16
        label(L1, y, "Additional Info")
        y += 13
        t(L1, y, v.get("add", v["ymm"]))
        y += 8

    y += 26
    head(L1, y, "Additional Info")
    y += 16
    label(L1, y, "Additional Terms")
    y += 13
    t(L1, y, "Call Pat 30 minutes before arrival.")
    y += 16
    label(L1, y, "Pre-Dispatch Notes from Customer")
    y += 13
    t(L1, y, "This load is released only to the carrier assigned.")
    y += 16
    label(L1, y, "Transport Special Instructions")
    y += 13
    t(L1, y, "Send pictures at pickup and delivery. Clear BOL required.")
    c.showPage()
    c.save()
    return buf.getvalue()


if __name__ == "__main__":
    import sys
    open(sys.argv[1] if len(sys.argv) > 1 else "cd_sample.pdf", "wb").write(build())
