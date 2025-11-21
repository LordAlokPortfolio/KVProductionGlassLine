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
# VALID TYPE DICTIONARY (REVISED)
# Used ONLY for suffix checking, NEVER for replacing the prefix
# =====================================================================

VALID_SUFFIXES = [
    "T", "E180ESC", "Q180", "E272", "Q272", "E366", "Q366",
    "EI89", "QI89", "NREEDV", "MATT", "GRY", "P621", "BRZ"
]

VALID_PREFIXES = ["3.1", "3.9", "4.7", "5.7"]

# =====================================================================
# IMAGE PREPROCESSING (OCR improvement)
# =====================================================================
def preprocess_image(img_bytes):
    img = Image.open(io.BytesIO(img_bytes))
    img = ImageOps.grayscale(img)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()

# =====================================================================
# OCR RAW TEXT (gpt-4o-mini)
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
                        {"type": "input_text", "text": "Extract RAW TEXT ONLY. No explanation."},
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
# TYPE EXTRACTION – NO PARAPHRASING
# Keep EXACT prefix (4.7 CL A E180 stays 4.7 CL A E180)
# Only fix spacing + suffix spelling
# =====================================================================
def extract_type(raw):

    # Look for anything starting with a valid prefix (3.1, 3.9, 4.7, 5.7)
    pattern = r"(3\.1|3\.9|4\.7|5\.7)[^\n]+"
    m = re.search(pattern, raw)
    if not m:
        return ""

    line = m.group(0).strip().replace("  ", " ")

    # Clean spacing between CL and A or similar patterns
    line = re.sub(r"\bCL\s+A\b", "CL A", line)  # ensure consistent spacing

    # Now we must verify suffix if present
    parts = line.split()

    # If only prefix present → CLEAR (return prefix only)
    if len(parts) == 1:
        return parts[0]

    # If prefix + 1 or more extra segments → keep EXACT STRING
    # But correct the final suffix spelling if it's close
    prefix = parts[0]
    suffix_parts = parts[1:]

    # Build suffix candidate
    suffix_raw = "".join(suffix_parts).upper().replace(" ", "")

    # Check if suffix matches allowed list
    best_suffix = None
    best_score = -1

    for valid in VALID_SUFFIXES:
        score = similarity(suffix_raw, valid)
        if score > best_score:
            best_score = score
            best_suffix = valid

    # If suffix loosely matches → correct it
    # Otherwise leave suffix AS IS
    if best_score > 0:
        # Rebuild type without altering prefix
        fixed = f"{prefix} " + " ".join(suffix_parts)
        # Replace only the last segment (suffix)
        fixed = re.sub(r"[A-Z0-9]+$", best_suffix, fixed)
        return fixed.strip()

    return line.strip()

# =====================================================================
# Similarity metric
# =====================================================================
def similarity(a, b):
    a, b = a.upper(), b.upper()
    score = sum(1 for x, y in zip(a, b) if x == y)
    return score

# =====================================================================
# SIZE EXTRACTION
# =====================================================================
def extract_size(raw):
    pat = r"(\d{2,4})\s*(\d+\/\d+).*?(\d{2,4})\s*(\d+\/\d+)"
    m = re.search(pat, raw)
    if not m:
        return ""
    w, wf, h, hf = m.groups()
    return f"{w} {wf} x {h} {hf}"

# =====================================================================
# TAG# (NO reconstruction)
# =====================================================================
def extract_tag(raw):
    m = re.search(r"\b\d{6}(?:[-.,]\d+)?\b", raw)
    return m.group(0) if m else ""

# =====================================================================
# PO# (Allow multiple matches, pick first)
# =====================================================================
def extract_po(raw):
    matches = re.findall(r"\b\d{3,6}-\d{3}\b", raw)
    return matches[0] if matches else ""

# =====================================================================
# SEND EMAIL (Table + CSV + Image)
# =====================================================================
def send_email(table_line, csv_bytes, img_bytes):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    if CC_EMAILS:
        msg["Cc"] = CC_EMAILS
    msg["Subject"] = f"Glass Damage Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    msg.set_content(table_line)

    msg.add_attachment(csv_bytes, maintype="text", subtype="csv", filename="glass_report.csv")
    msg.add_attachment(img_bytes, maintype="image", subtype="jpeg", filename="label.jpg")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)

# =====================================================================
# UI — SINGLE PAGE
# =====================================================================
st.title("KV Glass Damage Reporter")

mode = st.radio("Input Method:", ["Take Photo", "Upload Photo"])
img_bytes = None

if mode == "Take Photo":
    cam = st.camera_input("Capture Label")
    if cam:
        img_bytes = cam.getvalue()

if mode == "Upload Photo":
    f = st.file_uploader("Upload Image", type=["jpg", "jpeg", "png"])
    if f:
        img_bytes = f.read()

if img_bytes:
    st.image(img_bytes, caption="Preview", use_column_width=True)

    reason = st.selectbox("Reason", ["Scratched", "Broken", "Missing", "KV Production Issue"])
    notes = st.text_area("Notes (Qty must be included here)")

    if st.button("Process & Send"):
        with st.spinner("Extracting details …"):

            prep = preprocess_image(img_bytes)
            raw = ocr_raw_text(prep)

            size = extract_size(raw)
            gtype = extract_type(raw)
            tag = extract_tag(raw)
            po = extract_po(raw)

            qty_m = re.search(r"\b\d+\b", notes)
            qty = qty_m.group(0) if qty_m else ""

            # Horizontal table (ONE ROW)
            table_line = f"index,Size,Type,Tag#,PO#,Qty,Reason\n1,{size},{gtype},{tag},{po},{qty},{reason}"

            st.code(table_line)

            # CSV bytes
            csv_out = io.StringIO()
            writer = csv.writer(csv_out)
            writer.writerow(["index", "Size", "Type", "Tag#", "PO#", "Qty", "Reason"])
            writer.writerow(["1", size, gtype, tag, po, qty, reason])
            csv_bytes = csv_out.getvalue().encode("utf-8")

            send_email(table_line, csv_bytes, img_bytes)

            st.success("Report sent successfully!")
