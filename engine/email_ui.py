"""
Streamlit UI component for sending load report emails.

Usage in app.py:
    from engine.email_ui import render_email_panel
    render_email_panel(simulation_result, load_id, attachments)
"""
import streamlit as st
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

from engine.email_sender import (
    SMTPConfig,
    send_load_report,
    validate_email,
)


def render_email_panel(
    simulation_result: Dict[str, Any],
    load_id: str,
    attachments: Optional[List[Path]] = None,
    truck_type: str = "26ft",
):
    """
    Render the 'Send by email' expander panel in Streamlit.

    Args:
        simulation_result: dict from engine.best_packer.simulate()
        load_id: e.g. "L001"
        attachments: list of paths to attach (PDF, Excel)
        truck_type: for subject line
    """
    with st.expander("📧 Send by email", expanded=False):
        # Check config
        smtp_configured = bool(os.environ.get("SMTP_HOST")) and bool(
            os.environ.get("SMTP_FROM_ADDRESS")
        )

        if not smtp_configured:
            st.warning(
                "⚠️ SMTP not configured. Set `SMTP_HOST` and `SMTP_FROM_ADDRESS` "
                "environment variables. See `docs/EMAIL_SETUP.md`."
            )
            st.code(
                "export SMTP_HOST=smtp.office365.com\n"
                "export SMTP_FROM_ADDRESS=load-optimizer@lg.com\n"
                "export SMTP_USERNAME=load-optimizer@lg.com\n"
                "export SMTP_PASSWORD=<app password>",
                language="bash",
            )
            return

        # Recipient inputs
        col1, col2 = st.columns(2)
        with col1:
            to_str = st.text_input(
                "To (comma-separated)",
                placeholder="dock-manager@lg.com, driver@lg.com",
                key="email_to",
            )
        with col2:
            cc_str = st.text_input(
                "Cc (optional)",
                placeholder="planner@lg.com",
                key="email_cc",
            )

        subject = st.text_input(
            "Subject (auto if empty)",
            placeholder=f"[Load] {load_id} · {truck_type} · auto-generated",
            key="email_subject",
        )

        # Attachment selection
        if attachments:
            st.markdown("**Attachments**")
            selected_attachments = []
            for path in attachments:
                p = Path(path)
                size_kb = p.stat().st_size / 1024 if p.exists() else 0
                include = st.checkbox(
                    f"{p.name} ({size_kb:.0f} KB)",
                    value=True,
                    key=f"attach_{p.name}",
                )
                if include:
                    selected_attachments.append(p)
        else:
            selected_attachments = []
            st.caption("No attachments available")

        # Action buttons
        col_a, col_b, col_c = st.columns([1, 1, 2])

        def _parse_addresses(s: str) -> List[str]:
            return [a.strip() for a in s.split(",") if a.strip()]

        with col_a:
            preview_clicked = st.button("👁️ Preview", use_container_width=True)
        with col_b:
            send_clicked = st.button("📤 Send", type="primary", use_container_width=True)

        if preview_clicked or send_clicked:
            to_list = _parse_addresses(to_str)
            cc_list = _parse_addresses(cc_str)

            # Validation
            if not to_list:
                st.error("At least one recipient required")
                return
            invalid = [a for a in to_list + cc_list if not validate_email(a)]
            if invalid:
                st.error(f"Invalid email(s): {', '.join(invalid)}")
                return

            try:
                config = SMTPConfig.from_env()
                info = send_load_report(
                    config=config,
                    to=to_list,
                    cc=cc_list or None,
                    load_id=load_id,
                    simulation_result=simulation_result,
                    attachments=selected_attachments or None,
                    subject=subject or None,
                    truck_type=truck_type,
                    dry_run=preview_clicked,
                )

                if preview_clicked:
                    st.info(
                        f"**Preview (dry run)** — would send to {info['recipients']} "
                        f"recipient(s) with subject:\n\n_{info['subject']}_"
                    )
                    if info.get("attachments"):
                        st.caption(
                            "Attachments: " + ", ".join(info["attachments"])
                        )
                else:
                    st.success(
                        f"✅ Sent to {info['recipients']} recipient(s)\n\n"
                        f"Subject: _{info['subject']}_"
                    )

            except (ValueError, FileNotFoundError) as e:
                st.error(f"Validation error: {e}")
            except RuntimeError as e:
                st.error(f"Send failed: {e}")
            except EnvironmentError as e:
                st.error(f"Config error: {e}")
