"""
Tests for engine.email_sender.

Uses mock SMTP to avoid actual network calls.
"""
import pytest
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.email_sender import (
    SMTPConfig,
    send_load_report,
    validate_email,
    render_html_email,
    render_text_email,
)


@pytest.fixture
def fake_simulation_result():
    return {
        "fits": True,
        "strategy": "pair_height_desc",
        "requested_count": 36,
        "fitted_count": 36,
        "unfitted_count": 0,
        "unfitted_detail": [],
        "metrics": {
            "x_used_mm": 7370,
            "x_used_ft": 24.18,
            "compactness_pct": 93.0,
            "volume_util_pct": 52.72,
            "weight_total_kg": 3180.0,
            "weight_total_lb": 7011.5,
            "weight_util_pct": 70.67,
            "remaining_length_mm": 555,
            "remaining_length_ft": 1.82,
        },
        "placements": [
            {"seq": 1, "model_code": "LF29H8330S", "x_mm": 0, "y_mm": 0, "z_mm": 0,
             "dim_x_mm": 900, "dim_y_mm": 940, "dim_z_mm": 1850, "weight_kg": 155, "lane": 0, "layer": 0},
            {"seq": 2, "model_code": "LF29H8330S", "x_mm": 0, "y_mm": 940, "z_mm": 0,
             "dim_x_mm": 900, "dim_y_mm": 940, "dim_z_mm": 1850, "weight_kg": 155, "lane": 1, "layer": 0},
            {"seq": 7, "model_code": "WM4000HWA", "x_mm": 2700, "y_mm": 0, "z_mm": 0,
             "dim_x_mm": 830, "dim_y_mm": 745, "dim_z_mm": 1050, "weight_kg": 95, "lane": 0, "layer": 0},
            {"seq": 10, "model_code": "WM4000HWA", "x_mm": 2700, "y_mm": 0, "z_mm": 1050,
             "dim_x_mm": 830, "dim_y_mm": 745, "dim_z_mm": 1050, "weight_kg": 95, "lane": 0, "layer": 1},
        ],
    }


@pytest.fixture
def good_config():
    return SMTPConfig(
        host="smtp.test.example.com",
        port=587,
        username="planner@lg.com",
        password="dummy",
        from_address="load-optimizer@lg.com",
        from_name="LG Load Optimizer",
    )


# =============================================================================
# Email validation
# =============================================================================

class TestEmailValidation:
    @pytest.mark.parametrize("addr", [
        "test@lg.com",
        "first.last@lg.com",
        "user+tag@lg.example.co.kr",
        "user_name@lg-electronics.com",
    ])
    def test_valid_emails(self, addr):
        assert validate_email(addr)

    @pytest.mark.parametrize("addr", [
        "",
        "no_at_sign",
        "@lg.com",
        "no_domain@",
        "spaces in@lg.com",
        None,
    ])
    def test_invalid_emails(self, addr):
        assert not validate_email(addr)


# =============================================================================
# Config
# =============================================================================

class TestSMTPConfig:
    def test_from_env_success(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.office365.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "loader@lg.com")
        cfg = SMTPConfig.from_env()
        assert cfg.host == "smtp.office365.com"
        assert cfg.port == 587
        assert cfg.from_address == "loader@lg.com"

    def test_from_env_missing_host(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        with pytest.raises(EnvironmentError):
            SMTPConfig.from_env()

    def test_validate_requires_from_address(self):
        cfg = SMTPConfig(host="smtp.test.com")
        with pytest.raises(ValueError):
            cfg.validate()


# =============================================================================
# Rendering
# =============================================================================

class TestRendering:
    def test_html_contains_load_id(self, fake_simulation_result):
        html = render_html_email("L001", fake_simulation_result)
        assert "L001" in html
        assert "FITS" in html
        assert "24.18" in html  # length used

    def test_html_shows_unfitted_warning(self, fake_simulation_result):
        fake_simulation_result["fits"] = False
        fake_simulation_result["unfitted_count"] = 2
        fake_simulation_result["unfitted_detail"] = [
            {"model_code": "LWS3063ST", "quantity": 2}
        ]
        html = render_html_email("L002", fake_simulation_result)
        assert "DOES NOT FIT" in html
        assert "LWS3063ST" in html

    def test_text_email_includes_summary(self, fake_simulation_result):
        text = render_text_email("L001", fake_simulation_result)
        assert "L001" in text
        assert "FITS" in text
        assert "24.18" in text


# =============================================================================
# Send (with mock SMTP)
# =============================================================================

class TestSend:
    def test_dry_run_does_not_call_smtp(self, fake_simulation_result, good_config):
        with patch("smtplib.SMTP") as mock_smtp:
            result = send_load_report(
                config=good_config,
                to=["dock@lg.com"],
                load_id="L001",
                simulation_result=fake_simulation_result,
                dry_run=True,
            )
            assert result["dry_run"] is True
            assert result["sent"] is False
            assert result["recipients"] == 1
            mock_smtp.assert_not_called()

    def test_send_calls_smtp_with_tls(self, fake_simulation_result, good_config):
        with patch("smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            result = send_load_report(
                config=good_config,
                to=["dock@lg.com"],
                load_id="L001",
                simulation_result=fake_simulation_result,
            )

            assert result["sent"] is True
            assert result["recipients"] == 1
            mock_smtp.assert_called_once_with("smtp.test.example.com", 587, timeout=30)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("planner@lg.com", "dummy")
            mock_server.send_message.assert_called_once()

    def test_send_with_cc_and_bcc(self, fake_simulation_result, good_config):
        with patch("smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            result = send_load_report(
                config=good_config,
                to=["dock@lg.com"],
                cc=["planner@lg.com"],
                bcc=["audit@lg.com"],
                load_id="L001",
                simulation_result=fake_simulation_result,
            )

            assert result["recipients"] == 3

    def test_invalid_email_raises(self, fake_simulation_result, good_config):
        with pytest.raises(ValueError, match="Invalid email"):
            send_load_report(
                config=good_config,
                to=["not_an_email"],
                load_id="L001",
                simulation_result=fake_simulation_result,
            )

    def test_no_recipients_raises(self, fake_simulation_result, good_config):
        with pytest.raises(ValueError, match="No recipients"):
            send_load_report(
                config=good_config,
                to=[],
                load_id="L001",
                simulation_result=fake_simulation_result,
            )

    def test_missing_attachment_raises(self, fake_simulation_result, good_config):
        with pytest.raises(FileNotFoundError):
            send_load_report(
                config=good_config,
                to=["dock@lg.com"],
                load_id="L001",
                simulation_result=fake_simulation_result,
                attachments=["/nonexistent/file.pdf"],
                dry_run=True,
            )

    def test_attachment_included(self, fake_simulation_result, good_config, tmp_path):
        # Create a small test attachment
        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake content")

        result = send_load_report(
            config=good_config,
            to=["dock@lg.com"],
            load_id="L001",
            simulation_result=fake_simulation_result,
            attachments=[fake_pdf],
            dry_run=True,
        )
        assert "test.pdf" in result["attachments"]

    def test_smtp_failure_raises_runtime(self, fake_simulation_result, good_config):
        import smtplib
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.side_effect = smtplib.SMTPException("auth failed")
            with pytest.raises(RuntimeError, match="SMTP"):
                send_load_report(
                    config=good_config,
                    to=["dock@lg.com"],
                    load_id="L001",
                    simulation_result=fake_simulation_result,
                )
