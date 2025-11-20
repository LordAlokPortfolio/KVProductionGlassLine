import streamlit as st
import pytesseract
import cv2
import numpy as np
import re
from datetime import datetime
from email.message import EmailMessage
import smtplib
from PIL import Image
import io

# ==============================
# CONFIG / SECRETS
# ==============================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]

TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets["CC_EMAILS"]
ADMIN_PIN = st.secrets["ADMIN_PIN"]

# ==============================
# EMAIL SENDER
# ==============================
def send_email(subject, body, image_bytes):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    msg["Cc"] = CC_EMAILS
    msg["Subject"] = subject
    msg.set_content(body)

    msg.add_attachment(
        image_bytes,
        maintype="image",
        subtype="jpeg",
        filename="glass_label.jpg"
    )

    with smtplib.SMTP_SSL("smtp.mail.me.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)

# ==============================
# OCR PROCESSING (Tesseract)
# ==============================
def preprocess(image_bytes):
    image = Image.open(io.BytesIO(image_bytes)).convert("L")  # grayscale
    img_np = np.array(image)
    img_np = cv2.resize(img_np, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(img_np, (3, 3), 0)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 2
    )
    return thresh

def run_ocr(image_bytes):
    processed = preprocess(image_bytes)
    text = pytesseract.image_to_string(processed)
    return text.upper()

# ==============================
# FIELD EXTRACTION
# ==============================
GLASS_TOKENS = [
    "3.9 CLT", "CLT",
    "E180", "E185",
    "Q180", "Q185",
    "LOWE", "LOW E", "LOE",
    "I89",
    "LAMI", "LAMINATED",
    "CLEAR", "BRONZE"
]

def extract_fields(text):
    # TAG #
    tag_match = re.findall(r"([A-Z]{1,3}-)?(\d{4,6})", text)
    tag_num = tag_match[0][1] if tag_match else "NOT FOUND"

    # SIZE
    size_patterns = [
        r"\d+\s*\d*\/\d*\s*X\s*\d+\s*\d*\/\d*",   # fraction
        r"\d{2,4}\s*X\s*\d{2,4}"                  # mm
    ]
    sizes = set()
    for p in size_patterns:
        for m in re.findall(p, text):
            sizes.add(m)
    size_final = ", ".join(sizes) if sizes else "NOT FOUND"

    # QTY
    qty_match = re.search(r"QTY[: ]+(\d+)", text)
    qty_final = qty_match.group(1) if qty_match else "NOT FOUND"

    # GLASS TYPE
    types_found = []
    for token in GLASS_TOKENS:
        if token in text:
            types_found.append(token)
    glass_type_final = ", ".join(dict.fromkeys(types_found)) if types_found else "NOT FOUND"

    return tag_num, size_final, qty_final, glass_type_final

# ==============================
# UI SETTINGS
# ==============================
st.markdown("""
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
""", unsafe_allow_html=True)

# ==============================
# MENU
# ==============================
menu = st.sidebar.radio("Menu", ["Submit Report", "Admin Panel"])

# ==============================
# ADMIN PANEL
# ==============================
if menu == "Admin Panel":
    st.title("Admin Panel")

    pin = st.text_input("Enter Admin PIN", type="password")
    if pin != ADMIN_PIN:
        st.warning("Incorrect PIN")
        st.stop()

    st.success("Admin Access Granted")

    st.write("""
• Tesseract OCR Version Active  
• Fast Deployment Mode  
• No image storage (privacy safe)  
• All records are in email chain  
""")
    st.stop()

# ==============================
# SUBMISSION PAGE
# ==============================
st.title("KV Glass Damage Reporter (Tesseract OCR)")

photo = st.camera_input("Take Photo of Label")

reason_list = ["Scratched", "Broken", "Missing", "Wrong Size", "Wrong Type", "Other"]
reason = st.selectbox("Reason", reason_list)

notes = st.text_input("Additional Notes") if reason == "Other" else "None"

dept_map = {"PD": "Patio Door", "WD": "Window", "ED": "Entry Door", "SU": "Service Unit"}
dept_key = st.selectbox("Department", list(dept_map.keys()))

# ==============================
# SUBMIT
# ==============================
if st.button("Submit Report"):
    if not photo:
        st.error("Please take a photo first.")
        st.stop()

    img_bytes = photo.getvalue()

    ocr_text = run_ocr(img_bytes)
    tag_num, size_final, qty_final, glass_type_final = extract_fields(ocr_text)

    ref_id = datetime.now().strftime("%Y-%m-%d %H:%M")

    subject = f"Glass Damage Report – {dept_key} – {reason} – Ref {ref_id}"

    body = f"""
A glass has been found to be defective and is submitted by Production.

Extracted Label Details:
• Tag#: {tag_num}
• Size: {size_final}
• Qty Needed: {qty_final}
• Glass Type: {glass_type_final}

Manager Inputs:
• Department: {dept_key} ({dept_map[dept_key]})
• Reason: {reason}
• Additional Notes: {notes}

Photo of the label is attached.

Please review and forward to the glass vendor for the replacement and provide approximate ETA.

Reference ID: {ref_id}

Regards,
KV Production – Glass Line
"""

    try:
        send_email(subject, body, img_bytes)
        st.success("Report Sent Successfully.")
    except Exception as e:
        st.error(f"Email failed: {e}")
