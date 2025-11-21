import streamlit as st
import base64
from datetime import datetime
from email.message import EmailMessage
import smtplib
from openai import OpenAI
from PIL import Image, ImageEnhance, ImageOps
import io
import re
import csv

# =====================================================================
# LOAD SECRETS
# =====================================================================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets["CC_EMAILS"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================================
# VALID TYPES – MASTER DICTIONARY
# =====================================================================
VALID_TYPES = [
    "4.7A E180", "4.7A EI89",
    "3.9ANREEDV", "3.9A NREEDV",
    "3.1T Q366", "3.9T",
    "3.9T Q272", "3.1T Q180",
    "3.1T QI89", "3.0A E180ESC",
    "3.9A E180ESC", "3.9T Q180",
    "3.9T QI89", "3.9T Q366",
    "3.1T", "5.7A",
    "5.7A E366", "4.7A",
    "4.7A E366", "3.1T MATT",
    "3.1T QI89", "3.1T Q272",
    "3.1T RAINV", "3.9T RAINV"
]

# Create prefix families
A_PREFIXES = ("3.0A", "3.9A", "4.7A", "5.7A")
T_PREFIXES = ("3.1T", "3.9T")
NRE_PREFIX = ("3.9ANREEDV", "3.9A NREEDV")


# =====================================================================
# IMAGE PREPROCESSING (cropping, contrast, sharpness)
# =====================================================================
def preprocess_image(img_bytes):
    img = Image.open(io.BytesIO(img_bytes))

    # Convert to grayscale
    img = ImageOps.grayscale(img)

    # Increase contrast
    img = ImageEnhance.Contrast(img).enhance(2.0)

    # Slight sharpen
    img = ImageEnhance.Sharpness(img).enhance(1.5)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    return output.getvalue()


# =====================================================================
# OCR – STRICT GPT-4O-MINI RESPONSE FORMAT
# =====================================================================
def ocr_raw_text(img_bytes):
    b64 = base64.b64encode(img_bytes).decode("utf-8")

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Extract ONLY raw text. Do NOT explain."},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{b64}"
                        }
                    ]
                }
            ]
        )
        return response.output_text.strip()
    except Exception as e:
        return f"OCR_ERROR: {str(e)}"


# =====================================================================
# TYPE MATCHING ENGINE
# =====================================================================
def match_type(raw):

    # Normalize raw
    text = raw.replace("\n", " ").upper()

    # Try to detect prefix
    prefix_match = None

    for p in T_PREFIXES:
        if p in text:
            prefix_match = p
            family = "T"
            break

    for p in A_PREFIXES:
        if p in text:
            prefix_match = p
            family = "A"
            break

    for p in NRE_PREFIX:
        if p in text:
            prefix_match = p
            family = "NRE"
            break

    if not prefix_match:
        return ""

    # Filter dictionary by family
    if family == "T":
        candidates = [t for t in VALID_TYPES if t.startswith("3.1T") or t.startswith("3.9T")]
    elif family == "A":
        candidates = [t for t in VALID_TYPES if t.startswith(A_PREFIXES)]
    else:
        candidates = [t for t in VALID_TYPES if "NREEDV" in t]

    # Find best match inside family
    best = ""
    best_score = -1

    for c in candidates:
        score = similarity(text, c)
        if score > best_score:
            best_score = score
            best = c

    # If best is EXACT prefix only, type = CLEAR form
    if best in T_PREFIXES or best in A_PREFIXES:
        return best  # CLEAR type rule

    return best


# Simple similarity metric
def similarity(a, b):
    a = a.strip().upper()
    b = b.strip().upper()
    return sum(1 for x, y in zip(a, b) if x == y)


# =====================================================================
# SIZE EXTRACTION (fraction reconstruction)
# =====================================================================
def extract_size(raw):
    pat = r"(\d{2,4})\s*(\d+\/\d+).*?(\d{2,4})\s*(\d+\/\d+)"
    m = re.search(pat, raw)
    if not m:
        return ""
    w, wf, h, hf = m.groups()
    return f"{w} {wf} x {h} {hf}"


# =====================================================================
# PO# EXTRACTION
# =====================================================================
def extract_po(raw):
    m = re.search(r"\b\d{3,6}-\d{3}\b", raw)
    return m.group(0) if m else ""


# =====================================================================
# TAG# EXTRACTION (NO RECONSTRUCTION)
# =====================================================================
def extract_tag(raw):
    # Only extract the FIRST six-digit number and optional trailing symbols
    m = re.search(r"\b\d{6}(?:[-.,]\d+)?\b", raw)
    return m.group(0) if m else ""


# =====================================================================
# SEND EMAIL WITH TABLE + CSV
# =====================================================================
def send_email(table, csv_bytes, img_bytes):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    if CC_EMAILS:
        msg["Cc"] = CC_EMAILS
    msg["Subject"] = f"Glass Damage Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    msg.set_content(table)

    # CSV attachment
    msg.add_attachment(
        csv_bytes,
        maintype="text",
        subtype="csv",
        filename="glass_report.csv"
    )

    # Image attachment
    msg.add_attachment(
        img_bytes,
        maintype="image",
        subtype="jpeg",
        filename="label.jpg"
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)


# =====================================================================
# UI – SINGLE PAGE
# =====================================================================
st.title("KV Glass Damage Reporter – Final Version")

mode = st.radio("Choose input:", ["Take Photo", "Upload Photo"])

img_bytes = None

if mode == "Take Photo":
    cam = st.camera_input("Capture Label")
    if cam:
        img_bytes = cam.getvalue()

if mode == "Upload Photo":
    f = st.file_uploader("Upload label image", type=["jpg", "jpeg", "png"])
    if f:
        img_bytes = f.read()

# =====================================================================
# PROCESS
# =====================================================================
if img_bytes:

    st.image(img_bytes, caption="Preview", use_column_width=True)
    reason = st.selectbox("Reason", ["Scratched", "Broken", "Missing", "KV Production Issue"])
    notes = st.text_area("Notes (Qty must be included here)")

    if st.button("Process & Send"):
        with st.spinner("Extracting…"):

            # preprocess
            prep = preprocess_image(img_bytes)

            # OCR
            raw = ocr_raw_text(prep)

            # Extract fields
            size = extract_size(raw)
            gtype = match_type(raw)
            tag = extract_tag(raw)
            po = extract_po(raw)

            qty_m = re.search(r"\b\d+\b", notes)
            qty = qty_m.group(0) if qty_m else ""

            # Build table
            table = (
                "Glass Damage Report\n\n"
                "+-------+--------------------------+\n"
                "| Field | Value                    |\n"
                "+-------+--------------------------+\n"
                f"| Size  | {size} |\n"
                f"| Type  | {gtype} |\n"
                f"| Tag#  | {tag} |\n"
                f"| PO#   | {po} |\n"
                f"| Qty   | {qty} |\n"
                "+-------+--------------------------+\n"
            )

            st.code(table)

            # Create CSV
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["Field", "Value"])
            writer.writerow(["Size", size])
            writer.writerow(["Type", gtype])
            writer.writerow(["Tag#", tag])
            writer.writerow(["PO#", po])
            writer.writerow(["Qty", qty])

            csv_bytes = csv_buffer.getvalue().encode("utf-8")

            # Send Email
            send_email(table, csv_bytes, img_bytes)

            st.success("Report sent successfully!")
