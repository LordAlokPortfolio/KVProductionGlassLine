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

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================================
# SESSION STATE
# =====================================================================
if "batch" not in st.session_state:
    st.session_state.batch = []
if "camera_img" not in st.session_state:
    st.session_state.camera_img = None


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
# OCR (Correct 4o-mini format)
# =====================================================================
def run_ocr(img_bytes):
    try:
        b64 = base64.b64encode(img_bytes).decode("utf-8")

        response = client.responses.create(
            model="gpt-4o-mini",
            reasoning={"effort": "medium"},
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Extract all visible text from this image. Do not interpret. Raw OCR only."},
                        {
                            "type": "input_image",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                        }
                    ]
                }
            ]
        )

        return response.output_text.strip() if response.output_text else "OCR_ERROR: No text extracted."

    except Exception as e:
        return f"OCR_ERROR: {str(e)}"


# =====================================================================
# IMAGE PREVIEW HELPER
# =====================================================================
def show_image_preview(img_bytes, caption):
    img = Image.open(io.BytesIO(img_bytes))
    st.image(img, caption=caption, use_column_width=True)


# =====================================================================
# UI START
# =====================================================================
st.title("KV Glass Damage Reporter – DEV BRANCH")

tab1, tab2, tab3 = st.tabs(["Camera", "Upload", "Batch & Submit"])

# =====================================================================
# TAB 1 — CAMERA
# =====================================================================
with tab1:
    st.subheader("Take Photo")

    cam = st.camera_input("Capture Label")

    if cam:
        img_bytes = cam.getvalue()
        if len(img_bytes) < 5000:
            st.error("Camera image corrupted. Retake.")
        else:
            st.session_state.camera_img = img_bytes
            show_image_preview(img_bytes, "Captured Image")

            reason = st.selectbox(
                "Reason",
                ["Scratched", "Broken", "Missing", "KV Production Issue"]
            )
            notes = st.text_input("Notes")

            cols = st.columns(2)
            if cols[0].button("Add to Batch"):
                st.session_state.batch.append(
                    {"img": img_bytes, "reason": reason, "notes": notes}
                )
                st.success("Added to batch.")
                st.session_state.camera_img = None

            if cols[1].button("Retake Photo"):
                st.session_state.camera_img = None


# =====================================================================
# TAB 2 — UPLOAD
# =====================================================================
with tab2:
    st.subheader("Upload from Gallery")

    uploads = st.file_uploader(
        "Select images",
        accept_multiple_files=True,
        type=["jpg", "jpeg", "png"]
    )

    if uploads:
        for file in uploads:
            img_bytes = file.read()

            if len(img_bytes) < 5000:
                st.error(f"{file.name} corrupted. Skipped.")
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

            if st.button(f"Add {file.name}"):
                st.session_state.batch.append(
                    {"img": img_bytes, "reason": reason, "notes": notes}
                )
                st.success(f"{file.name} added.")


# =====================================================================
# TAB 3 — BATCH SUBMISSION
# =====================================================================
with tab3:
    st.subheader("Batch Review")

    if len(st.session_state.batch) == 0:
        st.info("No items in batch.")
    else:
        for idx, entry in enumerate(st.session_state.batch):
            st.write(f"### Photo {idx+1}")
            show_image_preview(entry["img"], f"{entry['reason']} | {entry['notes']}")

        if st.button("Submit Batch"):
            progress = st.progress(0)
            ocr_outputs = []

            for i, entry in enumerate(st.session_state.batch):
                progress.progress((i + 1) / len(st.session_state.batch))
                text = run_ocr(entry["img"])
                ocr_outputs.append(text)

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

            subject = f"Glass Damage Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"

            try:
                attachments = [x["img"] for x in st.session_state.batch]
                send_email(subject, email_body, attachments)
                st.success("Report sent successfully.")
                st.session_state.batch = []
            except Exception as e:
                st.error(f"Email failed: {e}")
