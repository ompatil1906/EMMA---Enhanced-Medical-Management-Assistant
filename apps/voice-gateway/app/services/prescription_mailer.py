"""Demo prescription email generator and SMTP dispatcher.

Dispatches NHS-styled demonstration prescriptions to patients' email addresses
upon confirmed prescription requests with verified Patient ID 110.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
from pathlib import Path
import smtplib
from typing import Any

logger = logging.getLogger(__name__)

SENT_PRESCRIPTIONS_DIR = Path("data/sent_prescriptions")


def _build_prescription_html(
    *,
    patient_name: str,
    patient_id: str,
    recipient_email: str,
    date_str: str,
) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>St. Jude Health Partners — Electronic Prescription</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f5f7; margin: 0; padding: 24px; color: #1e293b; }}
    .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.06); border: 1px solid #e2e8f0; }}
    .header {{ background: #005eb8; color: #ffffff; padding: 24px; text-align: left; }}
    .header h1 {{ margin: 0 0 4px; font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }}
    .header p {{ margin: 0; font-size: 13px; opacity: 0.9; }}
    .badge {{ display: inline-block; background: #e0f2fe; color: #0284c7; padding: 4px 10px; border-radius: 9999px; font-size: 11px; font-weight: 600; text-transform: uppercase; margin-top: 10px; }}
    .body {{ padding: 28px; }}
    .section-title {{ font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; color: #64748b; margin-bottom: 12px; border-bottom: 1px solid #f1f5f9; padding-bottom: 6px; }}
    .info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 24px; font-size: 13px; }}
    .info-label {{ color: #64748b; font-size: 11px; margin-bottom: 2px; text-transform: uppercase; font-weight: 600; }}
    .info-value {{ font-weight: 600; color: #0f172a; }}
    .rx-box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 20px; border-left: 4px solid #005eb8; }}
    .rx-name {{ font-size: 15px; font-weight: 700; color: #0f172a; margin-bottom: 4px; }}
    .rx-instructions {{ font-size: 13px; color: #334155; line-height: 1.5; }}
    .footer {{ background: #f8fafc; padding: 18px 24px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #f1f5f9; }}
    .demo-notice {{ background: #fef3c7; border: 1px solid #fde68a; color: #92400e; padding: 10px; border-radius: 6px; font-size: 11px; text-align: center; margin-top: 20px; font-weight: 500; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>St. Jude Health Partners</h1>
      <p>NHS Electronic Prescription Service (EPS)</p>
      <span class="badge">Repeat Prescription Approved</span>
    </div>

    <div class="body">
      <div class="section-title">Patient Identification</div>
      <div class="info-grid">
        <div>
          <div class="info-label">Patient Name</div>
          <div class="info-value">{patient_name}</div>
        </div>
        <div>
          <div class="info-label">Patient ID (Verified)</div>
          <div class="info-value">{patient_id}</div>
        </div>
        <div>
          <div class="info-label">Issue Date & Time</div>
          <div class="info-value">{date_str}</div>
        </div>
        <div>
          <div class="info-label">Sent To Email</div>
          <div class="info-value">{recipient_email}</div>
        </div>
      </div>

      <div class="section-title">Prescribed Medication</div>
      <div class="rx-box">
        <div class="rx-name">Amoxicillin 500mg Capsules (21 capsules)</div>
        <div class="rx-instructions"><strong>Dosage:</strong> Take ONE capsule THREE times daily with water, evenly spaced. Complete the full 7-day course.</div>
      </div>
      <div class="rx-box">
        <div class="rx-name">Paracetamol 500mg Tablets (32 tablets)</div>
        <div class="rx-instructions"><strong>Dosage:</strong> Take TWO tablets every 4-6 hours as required for pain/fever. Do not exceed 8 tablets in 24 hours.</div>
      </div>

      <div class="section-title">Authorisation Details</div>
      <div class="info-grid">
        <div>
          <div class="info-label">Authorising Clinician</div>
          <div class="info-value">Dr. Alistair Vance, MBChB, MRCGP</div>
        </div>
        <div>
          <div class="info-label">GMC Reference</div>
          <div class="info-value">#7129481</div>
        </div>
      </div>

      <div class="demo-notice">
        ⚠️ <strong>DEMONSTRATION ONLY:</strong> This is a synthetic demonstration prescription issued automatically by EMMA (Enhanced Medical Management Assistant) voice receptionist. It is NOT valid for pharmacy dispensing.
      </div>
    </div>

    <div class="footer">
      St. Jude Health Partners · NHS Practice Code: Y12048 · Automated EMMA Voice Gateway
    </div>
  </div>
</body>
</html>"""


def _send_smtp_sync(
    *,
    recipient: str,
    subject: str,
    html_content: str,
    text_content: str,
    host: str,
    port: int,
    user: str | None,
    password: str | None,
    sender: str,
    use_tls: bool,
) -> bool:
    """Synchronous SMTP delivery helper executed in threadpool."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10) as server:
            if user and password:
                server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if use_tls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
    return True


async def send_demo_prescription_email(
    *,
    to_email: str,
    patient_name: str,
    patient_id: str = "110",
    settings: Any = None,
) -> dict[str, Any]:
    """Send a demo prescription email via SMTP server, or save a local snapshot.

    Never raises into the voice call loop.
    """
    if not to_email or "@" not in to_email:
        logger.warning("Skipping prescription email: invalid or missing email (%s)", to_email)
        return {"status": "skipped", "reason": "invalid_email"}

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d %B %Y, %H:%M UTC")
    name = patient_name.strip() or "Patient"

    html = _build_prescription_html(
        patient_name=name,
        patient_id=patient_id,
        recipient_email=to_email,
        date_str=date_str,
    )
    plain_text = (
        f"St. Jude Health Partners — Electronic Prescription Service (EPS)\n"
        f"Repeat Prescription Approved\n\n"
        f"Patient: {name}\n"
        f"Patient ID: {patient_id}\n"
        f"Date: {date_str}\n\n"
        f"Rx 1: Amoxicillin 500mg Capsules (21 capsules) - Take 1 capsule 3 times daily.\n"
        f"Rx 2: Paracetamol 500mg Tablets (32 tablets) - Take 2 tablets every 4-6 hours as needed.\n\n"
        f"Prescriber: Dr. Alistair Vance, MBChB, MRCGP (GMC #7129481)\n"
        f"Notice: This is a DEMO prescription from the EMMA AI Voice Receptionist."
    )

    # Save a local HTML snapshot for immediate audit and testing
    snapshot_path = None
    try:
        SENT_PRESCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts_slug = now.strftime("%Y%m%d_%H%M%S")
        sanitized_email = to_email.replace("@", "_at_").replace(".", "_")
        snapshot_file = SENT_PRESCRIPTIONS_DIR / f"{ts_slug}_{sanitized_email}.html"
        snapshot_file.write_text(html, encoding="utf-8")
        snapshot_path = str(snapshot_file)
        logger.info("Saved demo prescription HTML snapshot to %s", snapshot_path)
    except Exception as exc:
        logger.warning("Could not save local prescription snapshot: %s", exc)

    # Resolve SMTP configuration
    smtp_host = getattr(settings, "SMTP_HOST", "localhost") if settings else "localhost"
    smtp_port = getattr(settings, "SMTP_PORT", 587) if settings else 587
    smtp_user = getattr(settings, "SMTP_USERNAME", None) if settings else None
    smtp_pass = getattr(settings, "SMTP_PASSWORD", None) if settings else None
    smtp_from = (
        getattr(settings, "SMTP_FROM", "prescriptions@stjudehealth.nhs.uk")
        if settings
        else "prescriptions@stjudehealth.nhs.uk"
    )
    smtp_tls = getattr(settings, "SMTP_USE_TLS", True) if settings else True

    subject = f"St. Jude Health Partners — Demo Prescription [Patient ID #{patient_id}]"

    try:
        await asyncio.to_thread(
            _send_smtp_sync,
            recipient=to_email,
            subject=subject,
            html_content=html,
            text_content=plain_text,
            host=smtp_host,
            port=smtp_port,
            user=smtp_user,
            password=smtp_pass,
            sender=smtp_from,
            use_tls=smtp_tls,
        )
        logger.info(
            "Demo prescription email successfully sent via SMTP (%s:%d) to %s",
            smtp_host,
            smtp_port,
            to_email,
        )
        return {
            "status": "sent",
            "recipient": to_email,
            "patient_id": patient_id,
            "snapshot_path": snapshot_path,
        }
    except Exception as exc:
        # In offline/local dev or without an active SMTP daemon, log gracefully and rely on the saved snapshot
        logger.info(
            "SMTP server (%s:%d) delivery unavailable (%s); prescription archived locally at %s",
            smtp_host,
            smtp_port,
            exc,
            snapshot_path,
        )
        return {
            "status": "saved_locally",
            "recipient": to_email,
            "patient_id": patient_id,
            "snapshot_path": snapshot_path,
            "smtp_error": str(exc),
        }


__all__ = ["send_demo_prescription_email"]
