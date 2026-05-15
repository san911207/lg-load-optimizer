"""
=============================================================================
Email Sender for LG Load Optimizer
=============================================================================

Sends load work orders to dock managers, drivers, and load planners.

Features:
  - SMTP (works with Microsoft 365, Gmail, or internal company relay)
  - HTML email with embedded simulation summary
  - File attachments (PDF work order, Excel report, 3D HTML)
  - TLS by default
  - Credentials via environment variables (never in code)

Usage (programmatic):
    from engine.email_sender import SMTPConfig, send_load_report

    config = SMTPConfig.from_env()  # reads SMTP_HOST, SMTP_PORT, etc.
    result = send_load_report(
        config=config,
        to=["dock-manager@lg.com"],
        cc=["planner@lg.com"],
        load_id="L001",
        simulation_result=result_dict,
        attachments=["outputs/L001.pdf", "outputs/load_report.xlsx"],
    )
    print(result)  # {'sent': True, 'recipients': 2, ...}

Usage (CLI):
    python -m engine.email_sender --load L001 \\
        --to dock@lg.com --cc planner@lg.com \\
        --attach outputs/L001.pdf outputs/load_report.xlsx
"""

import smtplib
import ssl
import os
import re
import json
import argparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field


# RFC 5322-ish (good enough for corporate addresses)
EMAIL_REGEX = re.compile(r"^[\w\.\-\+]+@[\w\.\-]+\.\w+$")
MAX_ATTACHMENT_SIZE_MB = 10
MAX_TOTAL_ATTACHMENT_MB = 20


@dataclass
class SMTPConfig:
    """SMTP configuration. Load from environment in production."""
    host: str
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = ""
    from_name: str = "LG Load Optimizer"

    @classmethod
    def from_env(cls) -> "SMTPConfig":
        """
        Build from environment variables.

        Required:
            SMTP_HOST           e.g. smtp.office365.com
            SMTP_FROM_ADDRESS   e.g. load-optimizer@lg.com

        Optional:
            SMTP_PORT           default 587
            SMTP_USERNAME       (if auth required)
            SMTP_PASSWORD       (if auth required)
            SMTP_USE_TLS        default "true"
            SMTP_FROM_NAME      default "LG Load Optimizer"
        """
        if "SMTP_HOST" not in os.environ:
            raise EnvironmentError(
                "SMTP_HOST not set. Configure SMTP via environment variables."
            )
        return cls(
            host=os.environ["SMTP_HOST"],
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=os.environ.get("SMTP_USERNAME", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() == "true",
            from_address=os.environ.get("SMTP_FROM_ADDRESS", ""),
            from_name=os.environ.get("SMTP_FROM_NAME", "LG Load Optimizer"),
        )

    def validate(self):
        if not self.host:
            raise ValueError("SMTP host is required")
        if not self.from_address:
            raise ValueError("from_address is required")
        if not validate_email(self.from_address):
            raise ValueError(f"Invalid from_address: {self.from_address}")


def validate_email(addr: str) -> bool:
    """Returns True if address looks like a valid email."""
    return bool(EMAIL_REGEX.match(addr or ""))


def render_html_email(load_id: str, result: Dict[str, Any], truck_type: str = "26ft") -> str:
    """HTML body for the email. Inline styles only (most email clients ignore CSS)."""
    m = result["metrics"]
    fits_badge = "✅ FITS" if result["fits"] else "⚠️ DOES NOT FIT"
    fits_color = "#1D9E75" if result["fits"] else "#A32D2D"

    # Aggregate placements per model
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for p in result["placements"]:
        by_model.setdefault(p["model_code"], []).append(p)

    zone_rows = ""
    for model, ps in by_model.items():
        rows = len(set(p["x_in"] for p in ps))
        lanes = len(set(p["lane"] for p in ps))
        layers = len(set(p["layer"] for p in ps))
        zone_rows += f"""
        <tr>
          <td style="padding:6px 10px;border-bottom:1px solid #E5E5E0;font-family:monospace;font-size:12px;">{model}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #E5E5E0;text-align:center;">{len(ps)}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #E5E5E0;text-align:center;font-family:monospace;font-size:12px;">{rows}R × {lanes}L × {layers}T</td>
        </tr>"""

    unfitted_section = ""
    if not result["fits"]:
        unfitted_items = ", ".join(
            f"{u['model_code']} × {u['quantity']}" for u in result.get("unfitted_detail", [])
        )
        unfitted_section = f"""
      <div style="background:#FBEAEA;border-left:3px solid #A32D2D;padding:10px 14px;border-radius:4px;margin:14px 0;">
        <strong style="color:#791F1F;">Unfitted units:</strong>
        <span style="color:#791F1F;">{unfitted_items}</span>
      </div>"""

    html = f"""<html>
<body style="font-family:-apple-system,Segoe UI,Helvetica,sans-serif;color:#3C3C3A;max-width:640px;margin:0 auto;padding:24px;background:#FAFAF7;">
  <div style="background:white;border-radius:8px;padding:24px;border:1px solid #E5E5E0;">
    <h1 style="font-size:20px;color:#191919;margin:0 0 4px 0;">Load Work Order — {load_id}</h1>
    <p style="color:#888780;font-size:13px;margin:0;">Automated by LG Load Optimizer · {truck_type} truck</p>

    <div style="display:inline-block;background:{fits_color};color:white;padding:6px 14px;border-radius:999px;font-size:13px;font-weight:500;margin:14px 0;">
      {fits_badge}
    </div>

    {unfitted_section}

    <h2 style="font-size:14px;color:#191919;margin-top:20px;border-bottom:1px solid #E5E5E0;padding-bottom:6px;">Summary</h2>
    <table style="width:100%;font-size:13px;border-collapse:collapse;">
      <tr><td style="padding:4px 0;color:#888780;">Units loaded</td><td style="text-align:right;font-weight:500;">{result['fitted_count']} / {result['requested_count']}</td></tr>
      <tr><td style="padding:4px 0;color:#888780;">Length used</td><td style="text-align:right;font-weight:500;">{m['x_used_ft']} ft <span style="color:#888780;">({m['compactness_pct']}%)</span></td></tr>
      <tr><td style="padding:4px 0;color:#888780;">Volume utilization</td><td style="text-align:right;font-weight:500;">{m['volume_util_pct']}%</td></tr>
      <tr><td style="padding:4px 0;color:#888780;">Weight</td><td style="text-align:right;font-weight:500;">{m['weight_total_lb']:,.0f} lb <span style="color:#888780;">({m['weight_util_pct']}%)</span></td></tr>
      <tr><td style="padding:4px 0;color:#888780;">Buffer (rear)</td><td style="text-align:right;font-weight:500;">{m['remaining_length_ft']} ft</td></tr>
    </table>

    <h2 style="font-size:14px;color:#191919;margin-top:20px;border-bottom:1px solid #E5E5E0;padding-bottom:6px;">Zone Breakdown</h2>
    <table style="width:100%;font-size:13px;border-collapse:collapse;">
      <thead>
        <tr style="background:#F8F7F2;">
          <th style="padding:6px 10px;text-align:left;font-weight:500;color:#888780;">Model</th>
          <th style="padding:6px 10px;text-align:center;font-weight:500;color:#888780;">Qty</th>
          <th style="padding:6px 10px;text-align:center;font-weight:500;color:#888780;">Layout (R × L × T)</th>
        </tr>
      </thead>
      <tbody>{zone_rows}
      </tbody>
    </table>

    <div style="margin-top:20px;padding:12px 14px;background:#FAEEDA;border-left:3px solid #BA7517;border-radius:4px;font-size:12px;color:#633806;">
      <strong>Reminders:</strong>
      <ul style="margin:6px 0 0 18px;padding:0;line-height:1.6;">
        <li>All boxes must remain upright (↑ arrows pointing up)</li>
        <li>Roll-up door track needs 10″ clearance in rear 5 ft</li>
        <li>Use 4 ratchet straps between zones + rear</li>
        <li>Refer to attached PDF for step-by-step worker guide</li>
      </ul>
    </div>

    <hr style="border:none;border-top:1px solid #E5E5E0;margin:24px 0 14px 0;">
    <p style="font-size:11px;color:#B8B7B0;margin:0;">
      Sent automatically by LG Load Optimizer · Internal use only · Do not reply to this address
    </p>
  </div>
</body>
</html>"""
    return html


def render_text_email(load_id: str, result: Dict[str, Any], truck_type: str = "26ft") -> str:
    """Plain-text fallback for email clients that strip HTML."""
    m = result["metrics"]
    fits = "FITS" if result["fits"] else "DOES NOT FIT"
    return f"""LG Load Optimizer — Work Order {load_id}
============================================

Truck: {truck_type}
Result: {fits}

Summary:
  Units loaded: {result['fitted_count']} / {result['requested_count']}
  Length used:  {m['x_used_ft']} ft ({m['compactness_pct']}%)
  Volume:       {m['volume_util_pct']}%
  Weight:       {m['weight_total_lb']:,.0f} lb ({m['weight_util_pct']}%)
  Buffer:       {m['remaining_length_ft']} ft

Attached:
  - PDF work order (print for dock)
  - Excel report (all details)

Reminders:
  - All boxes upright (verify ↑ arrows)
  - Roll-up door track needs 10" clearance in rear 5 ft
  - 4 ratchet straps between zones

—
Sent automatically by LG Load Optimizer.
Do not reply to this address.
""".strip()


def send_load_report(
    config: SMTPConfig,
    to: List[str],
    load_id: str,
    simulation_result: Dict[str, Any],
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    attachments: Optional[List[Path]] = None,
    subject: Optional[str] = None,
    truck_type: str = "26ft",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Send a load work order email.

    Args:
        config: SMTP configuration (use SMTPConfig.from_env() in production)
        to: list of recipient addresses
        load_id: load identifier (e.g. "L001")
        simulation_result: dict returned by engine.best_packer.simulate()
        cc: optional CC list
        bcc: optional BCC list
        attachments: optional list of file paths to attach
        subject: optional override (default: auto-generated)
        truck_type: for display only
        dry_run: if True, builds message but does NOT send (returns it for inspection)

    Returns:
        {
            "sent": bool,
            "dry_run": bool,
            "recipients": int,
            "subject": str,
            "attachments": [filename, ...],
            "message_id": str,
        }

    Raises:
        ValueError: invalid email or config
        FileNotFoundError: attachment missing
        RuntimeError: SMTP send failed
    """
    config.validate()

    # Validate addresses
    all_recipients = (to or []) + (cc or []) + (bcc or [])
    if not all_recipients:
        raise ValueError("No recipients specified")
    for addr in all_recipients:
        if not validate_email(addr):
            raise ValueError(f"Invalid email address: {addr}")

    # Build message
    msg = MIMEMultipart("mixed")
    msg["From"] = f"{config.from_name} <{config.from_address}>"
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)

    m = simulation_result["metrics"]
    fit_status = "FITS" if simulation_result["fits"] else "DOES NOT FIT"
    msg["Subject"] = subject or (
        f"[Load] {load_id} · {truck_type} · {fit_status} · {m['x_used_ft']}ft used"
    )

    # Body (multipart/alternative)
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(render_text_email(load_id, simulation_result, truck_type), "plain"))
    body.attach(MIMEText(render_html_email(load_id, simulation_result, truck_type), "html"))
    msg.attach(body)

    # Attachments
    attached_names = []
    total_size = 0
    for path in attachments or []:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Attachment not found: {path}")
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb > MAX_ATTACHMENT_SIZE_MB:
            raise ValueError(
                f"Attachment too large ({size_mb:.1f}MB > {MAX_ATTACHMENT_SIZE_MB}MB): {path.name}"
            )
        total_size += size_mb
        if total_size > MAX_TOTAL_ATTACHMENT_MB:
            raise ValueError(
                f"Total attachments exceed {MAX_TOTAL_ATTACHMENT_MB}MB: {total_size:.1f}MB"
            )

        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{path.name}"')
        msg.attach(part)
        attached_names.append(path.name)

    result_info = {
        "dry_run": dry_run,
        "recipients": len(all_recipients),
        "subject": msg["Subject"],
        "attachments": attached_names,
        "message_id": msg.get("Message-ID", ""),
    }

    if dry_run:
        result_info["sent"] = False
        result_info["preview"] = render_text_email(load_id, simulation_result, truck_type)[:200]
        return result_info

    # Send via SMTP with TLS
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(config.host, config.port, timeout=30) as server:
            server.ehlo()
            if config.use_tls:
                server.starttls(context=context)
                server.ehlo()
            if config.username:
                server.login(config.username, config.password)
            server.send_message(
                msg,
                from_addr=config.from_address,
                to_addrs=all_recipients,
            )
    except smtplib.SMTPException as e:
        raise RuntimeError(f"SMTP send failed: {e}") from e
    except (TimeoutError, OSError) as e:
        raise RuntimeError(f"SMTP connection failed: {e}") from e

    result_info["sent"] = True
    return result_info


# =============================================================================
# CLI
# =============================================================================

def _cli():
    """Command-line interface for ad-hoc sending.

    Example:
        python -m engine.email_sender \\
            --load L001 \\
            --result outputs/L001_result.json \\
            --to dock@lg.com --cc planner@lg.com \\
            --attach outputs/L001.pdf outputs/load_report.xlsx \\
            --dry-run
    """
    parser = argparse.ArgumentParser(description="Send load report email")
    parser.add_argument("--load", required=True, help="Load ID, e.g. L001")
    parser.add_argument("--result", required=True, type=Path,
                        help="Path to JSON file with simulate() output")
    parser.add_argument("--to", nargs="+", required=True, help="Recipient(s)")
    parser.add_argument("--cc", nargs="*", default=[], help="CC recipient(s)")
    parser.add_argument("--bcc", nargs="*", default=[], help="BCC recipient(s)")
    parser.add_argument("--attach", nargs="*", default=[], type=Path,
                        help="File(s) to attach (PDF, Excel, etc.)")
    parser.add_argument("--subject", help="Override subject line")
    parser.add_argument("--truck", default="26ft", help="Truck type label")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build message but do not send (preview only)")

    args = parser.parse_args()

    # Load simulation result
    result = json.loads(args.result.read_text())

    # Build config from env
    config = SMTPConfig.from_env()

    # Send
    info = send_load_report(
        config=config,
        to=args.to,
        cc=args.cc,
        bcc=args.bcc,
        load_id=args.load,
        simulation_result=result,
        attachments=args.attach,
        subject=args.subject,
        truck_type=args.truck,
        dry_run=args.dry_run,
    )

    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    _cli()
