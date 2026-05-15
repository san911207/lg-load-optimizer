# Email Setup Guide

LG Load Optimizer sends work order emails to dock managers, drivers, and planners. This document covers configuration for different SMTP providers.

## Quick reference

| Provider | Host | Port | Auth |
|----------|------|------|------|
| Microsoft 365 (Exchange Online) | smtp.office365.com | 587 | OAuth2 or App Password |
| Gmail | smtp.gmail.com | 587 | App Password |
| AWS SES | email-smtp.{region}.amazonaws.com | 587 | IAM SMTP credentials |
| SendGrid | smtp.sendgrid.net | 587 | API key as password |
| Internal relay (no auth) | mail.lg.internal | 25 or 587 | None (IP whitelist) |

## Environment variables

Set in `.env` (local dev) or your secret manager (production):

```bash
# Required
SMTP_HOST=smtp.office365.com
SMTP_FROM_ADDRESS=load-optimizer@lg.com

# Optional (defaults shown)
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_FROM_NAME="LG Load Optimizer"

# Authentication (required for most cloud providers)
SMTP_USERNAME=load-optimizer@lg.com
SMTP_PASSWORD=<from secret manager — never hardcode>
```

## Provider-specific setup

### Microsoft 365 (most likely at LG)

1. Have IT create a dedicated mailbox: `load-optimizer@lg.com` (or shared mailbox)
2. Enable SMTP AUTH for that mailbox (Exchange admin center → mailbox → manage email apps → SMTP)
3. Generate an **App Password** (if MFA is enforced):
   - User → Security → My sign-ins → App passwords → Create
   - OR use OAuth2 (more secure, but requires more setup)
4. Set environment:
   ```bash
   SMTP_HOST=smtp.office365.com
   SMTP_PORT=587
   SMTP_USERNAME=load-optimizer@lg.com
   SMTP_PASSWORD=<app password from step 3>
   SMTP_FROM_ADDRESS=load-optimizer@lg.com
   SMTP_USE_TLS=true
   ```

### Gmail (for testing)

1. Enable 2FA on the Google account
2. Generate App Password: https://myaccount.google.com/apppasswords
3. Set environment:
   ```bash
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=youraccount@gmail.com
   SMTP_PASSWORD=<16-char app password>
   SMTP_FROM_ADDRESS=youraccount@gmail.com
   ```

### AWS SES (if deployed on AWS)

1. Verify your sending domain in SES (TXT/DKIM/SPF records)
2. Move out of sandbox (request production access)
3. Create IAM user with `AmazonSesSendingAccess` policy
4. Generate SMTP credentials (different from IAM access key!):
   - SES console → SMTP settings → Create SMTP credentials
5. Set environment:
   ```bash
   SMTP_HOST=email-smtp.us-east-1.amazonaws.com
   SMTP_PORT=587
   SMTP_USERNAME=<SES SMTP username>
   SMTP_PASSWORD=<SES SMTP password>
   SMTP_FROM_ADDRESS=load-optimizer@lg.com  # must be verified
   ```

### Internal SMTP relay (no auth)

If LG has an internal mail relay (common in enterprises):

```bash
SMTP_HOST=mail.lg.internal       # ask IT
SMTP_PORT=25                     # or 587
SMTP_USE_TLS=false               # may not require TLS internally
# No username/password needed if IP-whitelisted
SMTP_FROM_ADDRESS=load-optimizer@lg.com
```

## Testing your config

### Dry run (no actual send)

```bash
# Set env first
export SMTP_HOST=smtp.office365.com
export SMTP_FROM_ADDRESS=load-optimizer@lg.com
export SMTP_USERNAME=load-optimizer@lg.com
export SMTP_PASSWORD=...

# Save a simulation result
python -c "
from engine.best_packer import simulate
import pandas as pd, json
xl='data/sample_input.xlsx'
master=pd.read_excel(xl,'Model_Master').set_index('model_code').to_dict('index')
master['LDFN4542S'].update({'stackable':True,'load_bear_kg':60,'fragile':False})
master['LWS3063ST'].update({'stackable':True,'load_bear_kg':90,'fragile':False})
truck=pd.read_excel(xl,'Truck_Master').set_index('truck_type').to_dict('index')['26ft']
orders=[
  {'model_code':'LF29H8330S','quantity':6},
  {'model_code':'WM4000HWA','quantity':8},
  {'model_code':'DLEX4000W','quantity':8},
  {'model_code':'LDFN4542S','quantity':10},
  {'model_code':'LWS3063ST','quantity':4},
]
result=simulate(orders,master,truck)
with open('outputs/L001_result.json','w') as f:
  json.dump(result,f)
"

# Preview the email without sending
python -m engine.email_sender \
  --load L001 \
  --result outputs/L001_result.json \
  --to dock@lg.com \
  --dry-run
```

### Actually send

```bash
# Remove --dry-run when ready
python -m engine.email_sender \
  --load L001 \
  --result outputs/L001_result.json \
  --to dock@lg.com \
  --cc planner@lg.com \
  --attach outputs/L001.pdf outputs/load_report.xlsx
```

## Programmatic usage

```python
from engine.best_packer import simulate
from engine.email_sender import SMTPConfig, send_load_report
from engine.pdf_gen import generate_work_order
from pathlib import Path

# Run simulation
result = simulate(orders, master, truck_spec)

# Generate PDF
pdf_bytes = generate_work_order(result, "L001")
Path("outputs/L001.pdf").write_bytes(pdf_bytes)

# Send email
config = SMTPConfig.from_env()
info = send_load_report(
    config=config,
    to=["dock-manager@lg.com"],
    cc=["load-planner@lg.com"],
    load_id="L001",
    simulation_result=result,
    attachments=["outputs/L001.pdf", "outputs/load_report.xlsx"],
)
print(f"Sent to {info['recipients']} recipients")
```

## Streamlit UI usage

The Streamlit app has a "Send email" panel:

1. Run simulation
2. Expand "Send by email" section
3. Enter recipients (comma-separated)
4. Optional: CC, custom subject
5. Click "Preview" first (dry run)
6. Click "Send" to deliver

## Security considerations

- **Never** commit `.env` to git (already in `.gitignore`)
- Use **app passwords** or **OAuth2**, never your primary password
- For production, use a **secret manager** (AWS Secrets Manager, Azure Key Vault, HashiCorp Vault) instead of env files
- **TLS is enforced** by default (`SMTP_USE_TLS=true`) — only disable for internal relays
- Recipient validation prevents typos but does not verify the address exists
- Attachment size capped at 10 MB per file, 20 MB total
- Audit log: every send is logged to stdout with recipient count and message ID (Phase 1: write to DB)

## Recipient list management (Phase 1)

For Phase 1, store recipient groups in DB:

```sql
CREATE TABLE email_recipients (
    id SERIAL PRIMARY KEY,
    role TEXT NOT NULL,         -- 'dock_manager', 'driver', 'planner'
    location_code TEXT,         -- e.g. 'NJ-DC1'
    email TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE
);
```

Then look up recipients by load destination automatically:

```python
recipients = db.query(EmailRecipient).filter_by(
    location_code=load.destination,
    role='dock_manager',
    active=True
).all()
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `SMTP authentication failed` | Wrong password / not app password | Generate app password, update SMTP_PASSWORD |
| `Connection refused` | Wrong host or firewall | Check SMTP_HOST, ask IT about firewall |
| `STARTTLS extension not supported` | Server doesn't support TLS | Set `SMTP_USE_TLS=false` for internal relays |
| `Sender address rejected` | FROM not authorized | Use mailbox you own, or whitelist with IT |
| Email lands in spam | Missing SPF/DKIM | IT must add DNS records for sending domain |
| `Recipient address rejected` | Typo or external domain blocked | Verify recipients, check corporate email policy |
