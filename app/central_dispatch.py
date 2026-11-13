"""Read a Central Dispatch dispatch sheet (PDF) into load fields.

CD dispatch sheets lay out each section as small grey labels ("Origin",
"Contact Info", "Total Price", ...) with the value printed underneath, in two
side-by-side halves (Shipper | Carrier, Origin | Destination, Dates | Load Info)
followed by a vehicle block per vehicle. We read the words with their positions,
find each label, and take the text directly below it in the same column.

The result only pre-fills the New load form; a person always reviews it.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pdfplumber

HEADINGS = ("Shipper Info", "Carrier Info", "Origin Info", "Destination Info", "Dates", "Load Info",
            "Vehicle Info", "Additional Info")
LABELS = {
    "shipper", "carrier", "contact info", "special number", "dispatch phone", "phone 2", "phone", "driver",
    "origin", "destination", "location type", "buyer reference number", "dispatch date",
    "scheduled pick-up", "scheduled pickup", "scheduled delivery", "load id", "order id", "total price",
    "payment terms", "vehicle year/make/model", "vin", "vehicle type", "color", "license state/#", "lot #",
    "additional info", "critical notes", "additional terms", "pre-dispatch notes from customer",
    "transport special instructions", "condition", "inop", "operable",
}
PHONE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?:\s*(?:x|ext\.?)\s*\d+)?")
CITY_ST_ZIP = re.compile(r"^(.*?),?\s+([A-Z]{2})\s+(\d{5})(?:-\d{4})?$")
DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
EMPTY = {"", "--", "-", "n/a", "na", "none"}


class NotADispatchSheet(ValueError):
    pass


# ---------------------------------------------------------------- reading the PDF

def _phrases(pdf):
    """Words grouped into on-screen phrases (words on one line, close together)."""
    out, yoff = [], 0.0
    for page in pdf.pages:
        words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
        words.sort(key=lambda w: (round(w["top"]), w["x0"]))
        rows = []
        for w in words:
            if rows and abs(rows[-1][0]["top"] - w["top"]) <= 2.5:
                rows[-1].append(w)
            else:
                rows.append([w])
        for row in rows:
            row.sort(key=lambda w: w["x0"])
            chunk = [row[0]]
            for w in row[1:]:
                if w["x0"] - chunk[-1]["x1"] > 7:
                    out.append(_mk(chunk, yoff, page.width))
                    chunk = [w]
                else:
                    chunk.append(w)
            out.append(_mk(chunk, yoff, page.width))
        yoff += float(page.height)
    out.sort(key=lambda p: (round(p["top"]), p["x0"]))
    return out


def _mk(words, yoff, width):
    return {"text": " ".join(w["text"] for w in words).strip(), "x0": words[0]["x0"], "x1": words[-1]["x1"],
            "top": words[0]["top"] + yoff, "bottom": words[0]["bottom"] + yoff, "width": float(width)}


def _key(p):
    return p["text"].strip().rstrip(":").lower()


def _is_heading(p, phrases):
    t = p["text"].strip()
    if t == "Additional Info":
        # a section heading is followed by labels; the vehicle field is followed by a value
        nxt = _below(p, phrases, 1)
        return bool(nxt) and _key(nxt[0]) in LABELS
    return any(t == h or t.startswith(h + " (") for h in HEADINGS)


def _below(p, phrases, n):
    return [q for q in phrases if q["top"] > p["bottom"] - 1 and abs(q["x0"] - p["x0"]) < 6][:n]


class Sheet:
    def __init__(self, phrases):
        self.p = phrases
        self.headings = [q for q in phrases if _is_heading(q, phrases)]
        heading_ids = {id(h) for h in self.headings}
        self.labels = [q for q in phrases if id(q) not in heading_ids and _key(q) in LABELS]
        self.label_ids = {id(q) for q in self.labels} | heading_ids
        self.text = "\n".join(q["text"] for q in phrases)

    def section(self, q):
        """Name of the heading that owns phrase q (nearest above, on q's half when the section is split)."""
        mid = q["width"] / 2
        best = None
        for h in self.headings:
            if h["top"] >= q["top"]:
                continue
            split = any(abs(o["top"] - h["top"]) < 4 and o is not h for o in self.headings)
            if split and ((h["x0"] < mid) != (q["x0"] < mid)):
                continue
            if best is None or h["top"] > best["top"]:
                best = h
        return best["text"].split(" (")[0] if best else None

    def value(self, lab):
        """Text printed under a label, within its column."""
        mid = lab["width"] / 2
        same_row = [o for o in self.labels if abs(o["top"] - lab["top"]) < 3 and o["x0"] > lab["x0"] + 2]
        split = self.section(lab) in ("Shipper Info", "Carrier Info", "Origin Info", "Destination Info",
                                      "Dates", "Load Info")
        right = min([o["x0"] for o in same_row] + [mid if (split and lab["x0"] < mid) else lab["width"]]) - 2
        left = lab["x0"] - 4
        lines, last = [], lab["bottom"]
        for q in self.p:
            if q["top"] <= lab["bottom"] - 1 or not (left <= q["x0"] < right):
                continue
            if id(q) in self.label_ids or q["top"] - last > 30:
                break
            lines.append(q["text"])
            last = q["bottom"]
        return [ln for ln in lines if ln.strip()]

    def get(self, name, section=None, nth=0):
        hits = [lab for lab in self.labels if _key(lab) == name and (section is None or self.section(lab) == section)]
        return self.value(hits[nth]) if len(hits) > nth else []

    def all(self, name, section=None):
        return [lab for lab in self.labels if _key(lab) == name and (section is None or self.section(lab) == section)]


# ---------------------------------------------------------------- field helpers

def _clean(lines):
    return [ln.strip() for ln in lines if ln.strip().lower() not in EMPTY]


def _first(lines):
    c = _clean(lines)
    return c[0] if c else None


def _phone(lines):
    for ln in lines:
        m = PHONE.search(ln)
        if m:
            return m.group(0)
    return None


def _date(lines):
    for ln in lines:
        m = DATE.search(ln)
        if m:
            mo, d, y = (int(x) for x in m.groups())
            y = y + 2000 if y < 100 else y
            try:
                return datetime(y, mo, d).date()
            except ValueError:
                return None
    return None


def _money(lines):
    for ln in lines:
        m = MONEY.search(ln)
        if m:
            try:
                return Decimal(m.group(1).replace(",", ""))
            except InvalidOperation:
                return None
    return None


def _place(lines):
    """[name?, street..., 'City, ST 12345'] -> dict"""
    lines = [ln.strip() for ln in lines if ln.strip()]
    out = {"name": None, "address": None, "city": None, "state": None, "zip": None}
    if not lines:
        return out
    idx = next((i for i in range(len(lines) - 1, -1, -1) if CITY_ST_ZIP.match(lines[i])), None)
    if idx is not None:
        m = CITY_ST_ZIP.match(lines[idx])
        out["city"], out["state"], out["zip"] = m.group(1).strip(" ,") or None, m.group(2), m.group(3)
        before = lines[:idx]
    else:
        before = lines
    if len(before) >= 2:
        name, street = before[0], " ".join(before[1:])
    elif len(before) == 1:
        # a lone line is the street if it starts with a number, else a place name
        name, street = (None, before[0]) if re.match(r"^\d", before[0]) else (before[0], None)
    else:
        name, street = None, None
    out["name"] = None if (name or "").lower() in EMPTY else name
    out["address"] = None if (street or "").lower() in EMPTY else street
    return out


def _contact(lines):
    c = _clean(lines)
    phone = _phone(c)
    name = next((ln for ln in c if not PHONE.search(ln)), None)
    return name, phone


def _terms(text):
    t = (text or "").lower()
    if "quick pay" in t or "quickpay" in t:
        return "quick_pay"
    if re.search(r"upon\s+pick[\s-]?up|on\s+pick[\s-]?up", t):
        return "cop"
    if re.search(r"upon\s+delivery|on\s+delivery|\bcod\b", t) and not re.search(r"business days|days of", t):
        return "cod"
    if re.search(r"business days|days of|invoice|ach|net\s*\d+", t):
        return "invoice"
    return "cod" if "cash" in t or "certified" in t else None


def _vehicle(ymm, vin, vtype, color, lot, extra):
    words = (ymm or "").split()
    year = words[0] if words and re.fullmatch(r"(19|20)\d\d", words[0]) else None
    rest = words[1:] if year else words
    make = rest[0] if rest else None
    model = " ".join(rest[1:]) or None
    vin = (vin or "").replace(" ", "").upper()
    blob = " ".join(x for x in (vtype, extra) if x).lower()
    inop = bool(re.search(r"\binop\b|inoperable|non[\s-]?runn?(?:ing|er)|does(?:n't| not) run", blob))
    return {"year": year, "make": make, "model": model[:60] if model else None,
            "vin": vin if VIN.fullmatch(vin) else (vin[:17] or None),
            "color": color, "lot_number": lot, "type": vtype, "operable": not inop}


# ---------------------------------------------------------------- main entry

def parse(fileobj):
    try:
        with pdfplumber.open(fileobj) as pdf:
            phrases = _phrases(pdf)
    except Exception as e:  # not a PDF, encrypted, corrupt…
        raise NotADispatchSheet("That file couldn't be read as a PDF.") from e
    s = Sheet(phrases)
    if not s.labels or not any(h["text"].startswith(("Origin Info", "Vehicle Info")) for h in s.headings):
        raise NotADispatchSheet("That PDF doesn't look like a Central Dispatch dispatch sheet.")

    shipper_lines = s.get("shipper", "Shipper Info")
    shipper = _place(shipper_lines)
    s_contact, s_phone = _contact(s.get("contact info", "Shipper Info"))
    pickup = _place(s.get("origin", "Origin Info"))
    p_contact, p_phone = _contact(s.get("contact info", "Origin Info"))
    delivery = _place(s.get("destination", "Destination Info"))
    d_contact, d_phone = _contact(s.get("contact info", "Destination Info"))

    load_id = _first(s.get("load id")) or _first(s.get("order id"))
    payment = " ".join(s.get("payment terms"))
    price = _money(s.get("total price")) or _money([payment])

    # vehicles: one block per "Vehicle Year/Make/Model" label
    ymm_labels = s.all("vehicle year/make/model")
    vehicles = []
    for i, ymm in enumerate(ymm_labels):
        top = ymm["top"] - 1
        bottom = ymm_labels[i + 1]["top"] - 1 if i + 1 < len(ymm_labels) else float("inf")

        def field(name, _top=top, _bottom=bottom):
            hit = next((lab for lab in s.labels if _key(lab) == name and _top <= lab["top"] < _bottom
                        and s.section(lab) == "Vehicle Info"), None)
            return _first(s.value(hit)) if hit else None

        vehicles.append(_vehicle(" ".join(s.value(ymm)), field("vin"), field("vehicle type"), field("color"),
                                 field("lot #"), field("additional info")))
    if not vehicles:  # fall back to any VINs printed on the sheet
        vehicles = [_vehicle("", v, None, None, None, None) for v in dict.fromkeys(VIN.findall(s.text))]

    notes = []
    for label, title in (("critical notes", "Critical notes"), ("additional terms", "Additional terms"),
                         ("transport special instructions", "Special instructions"),
                         ("pre-dispatch notes from customer", "Broker notes")):
        vals = []
        for lab in s.all(label):
            v = " ".join(_clean(s.value(lab)))
            if v and v not in vals:
                vals.append(v)
        if vals:
            notes.append(f"{title}: " + " | ".join(vals))
    loc_types = [_first(s.get("location type", sec)) for sec in ("Origin Info", "Destination Info")]

    return {
        "reference": load_id,
        "customer": {"name": shipper["name"] or _first(shipper_lines), "contact_name": s_contact,
                     "phone": _first(s.get("dispatch phone")) or s_phone,
                     "address": ", ".join(x for x in (shipper["address"],
                                                     " ".join(filter(None, [shipper["city"], shipper["state"],
                                                                            shipper["zip"]])))
                                          if x) or None,
                     "mc": _first(s.get("special number", "Shipper Info"))},
        "pickup": {**pickup, "contact": p_contact, "phone": p_phone,
                   "date": _date(s.get("scheduled pick-up") or s.get("scheduled pickup")),
                   "location_type": loc_types[0]},
        "delivery": {**delivery, "contact": d_contact, "phone": d_phone,
                     "date": _date(s.get("scheduled delivery")), "location_type": loc_types[1]},
        "price": price,
        "payment_terms": _terms(payment),
        "payment_text": payment or None,
        "vehicles": vehicles,
        "notes": "\n".join(notes) or None,
        "dispatch_date": _date(s.get("dispatch date")),
    }
