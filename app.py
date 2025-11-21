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
# SECRETS
# =====================================================================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets.get("CC_EMAILS", "")
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================================
# VALID SUFFIXES — STRICT (NO PARAPHRASING PREFIX)
# =====================================================================
VALID_SUFFIXES = [
    "T", "E180ESC", "Q180", "E272", "Q272",
    "E366", "Q366", "EI89", "QI89",
    "NREEDV", "MATT", "GRY", "P621", "BRZ"
]

PREFIX_SET = ["3.1", "3.9", "4.7", "5.7"]

# =====================================================================
# IMAGE PREPROCESSING
# =====================================================================
def preprocess(img_bytes):
    img = Image.open(io.BytesIO(img_bytes))
    img = ImageOps.grayscale(img)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()

# =====================================================================
# OCR RAW TEXT
# =====================================================================
def ocr_raw(img_bytes):
    b64 = base64.b64encode(img_bytes).decode()
    try:
        r = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Extract raw text only. No explanations."},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
                    ]
                }
            ]
        )
        return r.output_text.strip()
    except Exception as e:
        return f"OCR_ERROR: {e}"

# =====================================================================
# PREFIX REPAIR (Option B: fix ANY broken form)
# =====================================================================
def repair_prefix(text):
    patterns = {
        r"\b3\s*9\b": "3.9",
        r"\b39\b": "3.9",
        r"\b3[-/:,_ ]+9\b": "3.9",
        r"\b3\s*1\b": "3.1",
        r"\b31\b": "3.1",
        r"\b3[-/:,_ ]+1\b": "3.1",
        r"\b4\s*7\b": "4.7",
        r"\b47\b": "4.7",
        r"\b4[-/:,_ ]+7\b": "4.7",
        r"\b5\s*7\b": "5.7",
        r"\b57\b": "5.7",
        r"\b5[-/:,_ ]+7\b": "5.7",
    }
    for pat, rep in patterns.items():
        text = re.sub(pat, rep, text)
    return text

# =====================================================================
# TYPE EXTRACTION (NO PARAPHRASING)
# =====================================================================
def extract_type(raw):
    raw = repair_prefix(raw.replace("\n", " "))

    # Find the line containing prefix
    prefix_pat = r"(3\.1|3\.9|4\.7|5\.7)[^\n]+"
    m = re.search(prefix_pat, raw)
    if not m:
        return ""

    line = m.group(0)
    line = re.sub(r"\s+", " ", line).strip()

    # Clean CLT spacing
    line = re.sub(r"C\s*L\s*T", "CLT", line)

    parts = line.split()
    if len(parts) == 1:
        return parts[0].strip()

    # Fix suffix only — keep original ordering
    corrected = []
    for part in parts:
        part_clean = part.upper()

        # Fix suffix if needed
        best = part_clean
        best_score = -1
        for suf in VALID_SUFFIXES:
            s = similarity(part_clean, suf)
            if s > best_score:
                best_score = s
                best = suf

        # If match is weak → keep OCR version
        if best_score < 2:
            corrected.append(part)
        else:
            corrected.append(best)

    return " ".join(corrected).strip()

# =====================================================================
# Simple similarity score
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
# TAG EXTRACTION (NO RECONSTRUCTION)
# =====================================================================
def extract_tag(raw):
    m = re.search(r"\b\d{6}(?:[-.,]\d+)?\b", raw)
    return m.group(0) if m else ""

# =====================================================================
# PO EXTRACTION
# =====================================================================
def extract_po(raw):
    matches = re.findall(r"\b\d{3,6}-\d{3}\b", raw)
    return matches[0] if matches else ""

# =====================================================================
# SEND EMAIL WITH TABLE + CSV
# =====================================================================
def send_email(row_str, csv_bytes, img_bytes):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    if CC_EMAILS:
        msg["Cc"] = CC_EMAILS
    msg["Subject"] = f"Glass Damage Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    msg.set_content(row_str)

    msg.add_attachment(csv_bytes, maintype="text", subtype="csv", filename="glass_report.csv")

    msg.add_attachment(img_bytes, maintype="image", subtype="jpeg", filename="label.jpg")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)

# =====================================================================
# UI — ONE PAGE
# =====================================================================
st.title("KV Glass Damage Reporter")

mode = st.radio("Choose Method:", ["Take Photo", "Upload Photo"])
img_bytes = None

if mode == "Take Photo":
    cam = st.camera_input("Capture Label")
    if cam:
        img_bytes = cam.getvalue()

if mode == "Upload Photo":
    f = st.file_uploader("Upload Label Image", type=["jpg","jpeg","png"])
    if f:
        img_bytes = f.read()

if img_bytes:
    st.image(img_bytes, caption="Preview", use_column_width=True)

    reason = st.selectbox("Reason", ["Scratched","Broken","Missing","KV Production Issue"])
    notes = st.text_area("Notes (Qty required here)")

    if st.button("Process & Send"):
        with st.spinner("Extracting…"):

            prep = preprocess(img_bytes)
            raw = ocr_raw(prep)

            size = extract_size(raw)
            gtype = extract_type(raw)
            tag = extract_tag(raw)
            po = extract_po(raw)

            qty_m = re.search(r"\b\d+\b", notes)
            qty = qty_m.group(0) if qty_m else ""

            # One row table
            row = f"index | Size | Type | Tag# | PO# | Qty | Reason\n" \
                  f"1 | {size} | {gtype} | {tag} | {po} | {qty} | {reason}"

            st.code(row)

            # CSV
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["index","Size","Type","Tag#","PO#","Qty","Reason"])
            writer.writerow(["1", size, gtype, tag, po, qty, reason])
            csv_bytes = buf.getvalue().encode()

            send_email(row, csv_bytes, img_bytes)

            st.success("Report sent successfully!")
