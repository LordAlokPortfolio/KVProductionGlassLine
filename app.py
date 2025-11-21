import streamlit as st
import base64
from datetime import datetime
from email.message import EmailMessage
import smtplib
from openai import OpenAI
from PIL import Image
import io
import re

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
# OCR (strict gpt-4o-mini)
# =====================================================================
def ocr_text(img_bytes):
    b64 = base64.b64encode(img_bytes).decode("utf-8")

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Extract ONLY the raw text from this label. "
                                "Do not explain. Do not add words. Just dump exact text."
                            )
                        },
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
# DATA PARSING RULES
# =====================================================================
def extract_fields(raw):

    # TYPE → looks like “3.9 CLT Q180”
    type_match = re.search(r"\b\d\.\d\s*CLT\s*[A-Z0-9]+\b", raw)
    glass_type = type_match.group(0) if type_match else ""

    # PO# → choose exact 35789-000 style
    po_match = re.search(r"\b\d{3,6}-\d{3}\b", raw)
    po_num = po_match.group(0) if po_match else ""

    # TAG# → take full string around a 6-digit base
    # e.g., 172819-5,17,20 or 35789-000
    tag_match = re.search(r"\b\d{6}(?:[-.,]\d+)*\b", raw)
    tag_full = tag_match.group(0) if tag_match else ""

    # SIZE → reconstruct fractions
    # Look for patterns like: 42 5/16 and 85 7/16 with optional x
    size_pattern = r"(\d{2,4})\s*(\d{1,2}\/\d{1,2})\D+(\d{2,4})\s*(\d{1,2}\/\d{1,2})"
    size_match = re.search(size_pattern, raw)
    if size_match:
        w_int, w_frac, h_int, h_frac = size_match.groups()
        size = f"{w_int} {w_frac} x {h_int} {h_frac}"
    else:
        size = ""

    return size, glass_type, tag_full, po_num


# =====================================================================
# EMAIL SENDER
# =====================================================================
def send_email(subject, body, attachment_bytes):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    if CC_EMAILS:
        msg["Cc"] = CC_EMAILS
    msg["Subject"] = subject
    msg.set_content(body)

    msg.add_attachment(
        attachment_bytes,
        maintype="image",
        subtype="jpeg",
        filename="label.jpg"
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)


# =====================================================================
# UI – ONE PAGE ONLY
# =====================================================================
st.title("KV Glass Damage Reporter – Single Page")

mode = st.radio("Choose input method:", ["Take Photo", "Upload Photo"])

img_bytes = None

if mode == "Take Photo":
    cam = st.camera_input("Capture label")
    if cam:
        img_bytes = cam.getvalue()

if mode == "Upload Photo":
    file = st.file_uploader("Upload image", type=["jpg", "jpeg", "png"])
    if file:
        img_bytes = file.read()

# Only show next section if an image is present
if img_bytes:
    img_preview = Image.open(io.BytesIO(img_bytes))
    st.image(img_preview, caption="Selected Image", use_column_width=True)

    st.subheader("Enter Details")

    reason = st.selectbox("Reason", ["Scratched", "Broken", "Missing", "KV Production Issue"])
    notes = st.text_area("Notes (Qty must be included here)")

    if st.button("Extract Details + Submit"):
        with st.spinner("Processing..."):
            raw = ocr_text(img_bytes)

            size, gtype, tag_full, po_num = extract_fields(raw)

            # Pull qty from notes (extract first number)
            qty_match = re.search(r"\b\d+\b", notes)
            qty = qty_match.group(0) if qty_match else ""

            # Build table
            table = f"""
Glass Damage Report

| Field | Value |
|-------|-------|
| Size | {size} |
| Type | {gtype} |
| Tag# | {tag_full} |
| PO# | {po_num} |
| Qty | {qty} |
"""

            st.markdown(table)

            # Email
            try:
                send_email(
                    subject=f"Glass Damage Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    body=table,
                    attachment_bytes=img_bytes
                )
                st.success("Report sent successfully.")
            except Exception as e:
                st.error(f"Email failed: {e}")
