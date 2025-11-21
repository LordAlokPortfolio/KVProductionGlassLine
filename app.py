import streamlit as st
import base64
import time
from datetime import datetime
from email.message import EmailMessage
import smtplib
from openai import OpenAI
from PIL import Image
import io

# =====================================================================
# LOAD SECRETS
# =====================================================================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets["CC_EMAILS"]
ADMIN_PIN = st.secrets["ADMIN_PIN"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

# OpenAI Client
client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================================
# SESSION STATE (Batch Storage)
# =====================================================================
if "batch" not in st.session_state:
    st.session_state.batch = []  # each entry = {img_bytes, reason, notes}
if "camera_img" not in st.session_state:
    st.session_state.camera_img = None
if "gallery_imgs" not in st.session_state:
    st.session_state.gallery_imgs = []

# =====================================================================
# EMAIL SENDER
# =====================================================================
def send_email(subject, body, attachments):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    if CC_EMAILS:
        msg["Cc"] = CC_EMAILS
    msg["Subject"] = subject
    msg.set_content(body)

    # Attach all images in batch
    for idx, img_bytes in enumerate(attachments):
        msg.add_attachment(
            img_bytes,
            maintype="image",
            subtype="jpeg",
            filename=f"label_{idx+1}.jpg"
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)

# =====================================================================
# OCR FUNCTION (OpenAI Vision)
# =====================================================================
def run_ocr(img_bytes):
    try:
        b64 = base64.b64encode(img_bytes).decode("utf-8")

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract ALL text from this image. Do not interpret, only raw OCR."
                        },
                        {
                            "type": "image_url",
                            "image_url": f"data:image/jpeg;base64,{b64}"
                        }
                    ]
                }
            ],
            max_tokens=500
        )

        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"OCR_ERROR: {e}"


# =====================================================================
# IMAGE PREVIEW HELPER
# =====================================================================
def show_image_preview(img_bytes, caption):
    img = Image.open(io.BytesIO(img_bytes))
    st.image(img, caption=caption, use_column_width=True)

# =====================================================================
# UI START
# =====================================================================
st.title("KV Glass Damage Reporter – Hybrid Camera + Gallery")

tab1, tab2, tab3 = st.tabs(["Take Photo", "Upload Photo", "Batch & Submit"])

# =====================================================================
# TAB 1 — CAMERA
# =====================================================================
with tab1:
    st.subheader("Take Photo")

    cam = st.camera_input("Capture Label")

    if cam:
        img_bytes = cam.getvalue()
        if len(img_bytes) < 5000:
            st.error("Camera image is corrupted. Retake.")
        else:
            st.session_state.camera_img = img_bytes
            show_image_preview(img_bytes, "Captured Image")

            reason = st.selectbox(
                "Reason for this glass",
                ["Scratched", "Broken", "Missing", "KV Production Issue"]
            )
            notes = st.text_input("Notes (optional)")

            if st.button("Add to Batch (Camera Photo)"):
                st.session_state.batch.append(
                    {"img": img_bytes, "reason": reason, "notes": notes}
                )
                st.success("Added to batch.")
                st.session_state.camera_img = None

# =====================================================================
# TAB 2 — GALLERY
# =====================================================================
with tab2:
    st.subheader("Upload from Gallery")

    uploads = st.file_uploader(
        "Select one or more images",
        accept_multiple_files=True,
        type=["jpg", "jpeg", "png"]
    )

    if uploads:
        for file in uploads:
            img_bytes = file.read()
            if len(img_bytes) < 5000:
                st.error(f"{file.name} is corrupted. Skip.")
                continue

            show_image_preview(img_bytes, file.name)

            reason = st.selectbox(
                f"Reason for {file.name}",
                ["Scratched", "Broken", "Missing", "KV Production Issue"],
                key=f"reason_{file.name}"
            )
            notes = st.text_input(
                f"Notes for {file.name}",
                key=f"notes_{file.name}"
            )

            if st.button(f"Add {file.name} to Batch"):
                st.session_state.batch.append(
                    {"img": img_bytes, "reason": reason, "notes": notes}
                )
                st.success(f"{file.name} added to batch.")

# =====================================================================
# TAB 3 — BATCH & SUBMIT
# =====================================================================
with tab3:
    st.subheader("Batch Review")

    if len(st.session_state.batch) == 0:
        st.info("No photos in batch yet.")
    else:
        for idx, entry in enumerate(st.session_state.batch):
            st.write(f"### Photo {idx+1}")
            show_image_preview(entry["img"], f"Reason: {entry['reason']} / Notes: {entry['notes']}")

        # Perform OCR on submit
        if st.button("Submit Batch"):
            with st.spinner("Running OCR on all images..."):
                ocr_outputs = []
                for entry in st.session_state.batch:
                    text = run_ocr(entry["img"])
                    ocr_outputs.append(text)

            # Build email body
            email_body = "A glass damage report has been submitted.\n\n"
            for idx, (entry, ocr_text) in enumerate(zip(st.session_state.batch, ocr_outputs)):
                email_body += f"""
==========================
Photo {idx+1}
==========================
Reason: {entry['reason']}
Notes: {entry['notes']}

OCR Extracted Text:
{ocr_text}

"""

            subject = (
                f"Glass Damage Batch Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

            # Send email
            try:
                attachments = [x["img"] for x in st.session_state.batch]
                send_email(subject, email_body, attachments)
                st.success("Email sent successfully.")

                # Auto-clear batch
                st.session_state.batch = []

            except Exception as e:
                st.error(f"Email failed: {e}")
