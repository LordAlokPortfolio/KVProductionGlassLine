import streamlit as st
import requests
import base64
import re
from datetime import datetime
from email.message import EmailMessage
import smtplib
from PIL import Image
import io
import smtplib

# ==========================================
# CONFIG (STREAMLIT SECRETS)
# ==========================================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]

TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets["CC_EMAILS"]
ADMIN_PIN = st.secrets["ADMIN_PIN"]

OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

# ==========================================
# OPENAI VISION OCR
# ==========================================
def extract_with_openai(image_bytes):
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Extract the following fields from this glass label:\n"
                            "- TAG# (digits only, ignore PD/WD/etc.)\n"
                            "- SIZE (inches or mm)\n"
                            "- QTY\n"
                            "- GLASS TYPE (CLT, LOWE, E180, Q180, I89, LAMI, CLEAR, BRONZE)\n"
                            "Respond in JSON ONLY with keys: tag, size, qty, glass_type."
                        )
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64}"
                    }
                ]
            }
        ],
        "max_output_tokens": 200
    }

    res = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"},
        json=payload
    )

    out = res.json()
    
    # Extract JSON from response
    try:
        text = out["output_text"]
        import json
        return json.loads(text)
    except:
        return {"tag": "NOT FOUND", "size": "NOT FOUND", "qty": "NOT FOUND", "glass_type": "NOT FOUND"}

# ==========================================
# EMAIL SENDER
# ==========================================
from email.message import EmailMessage

def send_email(subject, body, image_bytes):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"

    to_list = [e.strip() for e in st.secrets["TO_EMAILS"].split(",") if e.strip()]
    cc_list = [e.strip() for e in st.secrets["CC_EMAILS"].split(",") if e.strip()]

    msg["To"] = ", ".join(to_list)
    msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.set_content(body)

    msg.add_attachment(
        image_bytes,
        maintype="image",
        subtype="jpeg",
        filename="glass_label.jpg"
    )

    all_recipients = to_list + cc_list

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg, to_addrs=all_recipients)

# ==========================================
# UI SETTINGS (iPhone optimized)
# ==========================================
st.markdown("""
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="viewport" content="width=device-width, initial-scale=1">
""", unsafe_allow_html=True)

menu = st.sidebar.radio("Menu", ["Submit Report", "Admin Panel"])

# ==========================================
# ADMIN PANEL
# ==========================================
if menu == "Admin Panel":
    st.title("Admin Panel")

    pin = st.text_input("Enter Admin PIN", type="password")
    if pin != ADMIN_PIN:
        st.warning("Incorrect PIN")
        st.stop()

    st.success("Admin Access Granted")
    st.write("OpenAI Vision OCR Active. No local OCR dependencies.")
    st.stop()

# ==========================================
# MAIN PAGE
# ==========================================
st.title("KV Glass Damage Reporter (OpenAI Vision OCR)")

photo = st.camera_input("Take Photo of Label")

reason = st.selectbox("Reason", 
    ["Scratched", "Broken", "Missing", "Wrong Size", "Wrong Type", "Other"]
)

notes = st.text_input("Additional Notes") if reason == "Other" else "None"

dept_map = {"PD": "Patio Door", "WD": "Window", "ED": "Entry Door", "SU": "Service Unit"}
dept_key = st.selectbox("Department", list(dept_map.keys()))

# ==========================================
# SUBMIT
# ==========================================
if st.button("Submit Report"):
    if not photo:
        st.error("Please take a photo first.")
        st.stop()

    img_bytes = photo.getvalue()

    # RUN OPENAI OCR
    with st.spinner("Reading label…"):
        info = extract_with_openai(img_bytes)

    tag = info.get("tag", "NOT FOUND")
    size = info.get("size", "NOT FOUND")
    qty = info.get("qty", "NOT FOUND")
    gtype = info.get("glass_type", "NOT FOUND")

    ref = datetime.now().strftime("%Y-%m-%d %H:%M")

    subject = f"Glass Damage Report – {dept_key} – {reason} – Ref {ref}"

    body = f"""
A glass has been found defective.

Extracted Label Details (via OpenAI Vision):
• Tag#: {tag}
• Size: {size}
• Qty Needed: {qty}
• Glass Type: {gtype}

Manager Inputs:
• Department: {dept_key} ({dept_map[dept_key]})
• Reason: {reason}
• Additional Notes: {notes}

Reference ID: {ref}

Regards,
KV Production – Glass Line
"""

    try:
        send_email(subject, body, img_bytes)
        st.success("Sent successfully.")
    except Exception as e:
        st.error(f"Email failed: {e}")
