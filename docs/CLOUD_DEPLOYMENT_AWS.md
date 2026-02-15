# Cloud Deployment Runbook (Recommended: AWS)

This guide deploys the full system on cloud infrastructure:

- Trading bot runtime
- Dashboard UI/API
- Persistent storage for logs/state
- Managed PostgreSQL for analytics/reporting infrastructure

## 1. Recommendation

Recommended stack:

1. `AWS EC2` for bot + dashboard compute
2. `AWS RDS PostgreSQL` for database infrastructure
3. `AWS EBS` for persistent runtime files (`token.json`, logs, model state)
4. `Nginx + Let's Encrypt` on the VM for secure dashboard access over HTTPS

Why this stack:

- Reliable US-region infra and simple scaling path
- Stable long-running process control (systemd)
- Managed DB and backups (RDS)
- Easy to secure with VPC + Security Groups

## 2. Architecture

- EC2 VM runs one `systemd` service: `python -m ai_trader_bot`
- Dashboard is served by the bot process (same runtime)
- Nginx reverse-proxies `https://your-domain` -> `http://127.0.0.1:8787`
- Runtime writes JSONL/state files to mounted persistent disk (EBS)
- Optional: analytics pipelines read those JSONL files into PostgreSQL

Important: current bot code is file-backed (JSONL/state files), not SQL-backed for core execution.
Use PostgreSQL as analytics/reporting infra first, then migrate runtime writes later if desired.

## 3. Prerequisites

1. AWS account
2. Domain name for dashboard (recommended)
3. Schwab app credentials
4. Local machine with this repo

## 4. Provision Cloud Resources

## 4.1 EC2

1. Region: `us-east-1` (or closest US region)
2. OS: `Ubuntu 22.04 LTS`
3. Size for starting point: `4 vCPU / 8-16 GB RAM` (for example `c7i.xlarge` or similar)
4. Disk:
   - root: 30 GB
   - additional EBS volume: 100+ GB (`gp3`) for data
5. Security Group:
   - TCP 22 from your IP only
   - TCP 443 from internet
   - Optional TCP 80 (for TLS setup)
   - Do **not** expose 8787 publicly

## 4.2 RDS PostgreSQL (managed DB infra)

1. Create PostgreSQL 16 instance
2. Place in same VPC/private subnet
3. Restrict inbound to EC2 security group only
4. Create database/user:
   - DB: `ai_trader`
   - User: `ai_trader_app`
5. Enable automated backups

## 5. VM Bootstrap

SSH into EC2:

```bash
ssh -i /path/to/key.pem ubuntu@<EC2_PUBLIC_IP>
```

Install runtime:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git nginx certbot python3-certbot-nginx
```

Mount data EBS volume (example device `/dev/nvme1n1`):

```bash
sudo mkfs -t ext4 /dev/nvme1n1
sudo mkdir -p /srv/ai-trader-data
sudo mount /dev/nvme1n1 /srv/ai-trader-data
sudo chown -R ubuntu:ubuntu /srv/ai-trader-data
echo '/dev/nvme1n1 /srv/ai-trader-data ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
```

## 6. Deploy App Code

```bash
mkdir -p ~/apps
cd ~/apps
git clone <YOUR_REPO_URL> auto_investing
cd auto_investing
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 7. Configure Environment

Create env file:

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

- API/auth:
  - `SCHWAB_API_KEY`
  - `SCHWAB_APP_SECRET`
  - `SCHWAB_CALLBACK_URL`
  - `OPENAI_API_KEY` (required for the LLM-first planner; defaults keep LLM-first enabled)
- Runtime mode:
  - `LIVE_TRADING=false` for initial validation
- Dashboard:
  - `ENABLE_DASHBOARD=true`
  - `DASHBOARD_HOST=127.0.0.1`
  - `DASHBOARD_PORT=8787`
- Paths (point to persistent EBS mount):
  - `SCHWAB_TOKEN_PATH=/srv/ai-trader-data/token.json`
  - `RUNTIME_STATE_PATH=/srv/ai-trader-data/runtime_state.json`
  - `AI_LONG_TERM_STATE_PATH=/srv/ai-trader-data/long_term_state.json`
  - `HISTORICAL_RESEARCH_STATE_PATH=/srv/ai-trader-data/historical_research_state.json`
  - `MACRO_LONG_TERM_STATE_PATH=/srv/ai-trader-data/macro_long_term_state.json`
  - `DECISION_LEARNING_STATE_PATH=/srv/ai-trader-data/decision_learning_state.json`
  - `REPORT_STATE_PATH=/srv/ai-trader-data/report_state.json`
  - `DAILY_REPORT_LOG_PATH=/srv/ai-trader-data/daily_report.jsonl`
  - `WEEKLY_REPORT_LOG_PATH=/srv/ai-trader-data/weekly_report.jsonl`
  - `RESEARCH_LOG_PATH=/srv/ai-trader-data/research_log.jsonl`
  - `ACTIVITY_LOG_PATH=/srv/ai-trader-data/activity_log.jsonl`
  - `PORTFOLIO_LOG_PATH=/srv/ai-trader-data/portfolio_log.jsonl`
  - `METADATA_LOG_PATH=/srv/ai-trader-data/metadata_log.jsonl`
  - `SYSTEM_LOG_PATH=/srv/ai-trader-data/system.log`

Optional DB infra envs (for future integrations/jobs):

- `POSTGRES_HOST=<rds-endpoint>`
- `POSTGRES_PORT=5432`
- `POSTGRES_DB=ai_trader`
- `POSTGRES_USER=ai_trader_app`
- `POSTGRES_PASSWORD=<secret>`

## 8. Schwab Token Handling (important)

Because OAuth can require interactive login, easiest production path:

1. Generate `token.json` on your local machine first.
2. Copy to VM securely:

```bash
scp -i /path/to/key.pem token.json ubuntu@<EC2_PUBLIC_IP>:/srv/ai-trader-data/token.json
```

3. Restrict permissions:

```bash
chmod 600 /srv/ai-trader-data/token.json
```

## 9. Configure systemd Service

Copy service template from repo:

```bash
sudo cp deploy/systemd/ai-trader.service /etc/systemd/system/ai-trader.service
sudo sed -i 's|__APP_DIR__|/home/ubuntu/apps/auto_investing|g' /etc/systemd/system/ai-trader.service
sudo sed -i 's|__ENV_FILE__|/home/ubuntu/apps/auto_investing/.env|g' /etc/systemd/system/ai-trader.service
sudo systemctl daemon-reload
sudo systemctl enable ai-trader
```

Start in dry-run first:

```bash
sudo systemctl start ai-trader
sudo systemctl status ai-trader --no-pager
journalctl -u ai-trader -f
```

## 10. Configure Nginx + TLS for Dashboard

Copy Nginx config:

```bash
sudo cp deploy/nginx/ai-trader-dashboard.conf /etc/nginx/sites-available/ai-trader-dashboard
sudo ln -s /etc/nginx/sites-available/ai-trader-dashboard /etc/nginx/sites-enabled/ai-trader-dashboard
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Edit server name:

```bash
sudo sed -i 's/__DOMAIN__/your-domain.com/g' /etc/nginx/sites-available/ai-trader-dashboard
sudo nginx -t && sudo systemctl reload nginx
```

Issue certificate:

```bash
sudo certbot --nginx -d your-domain.com
```

Dashboard URL:

- `https://your-domain.com`

## 11. Validation Checklist

1. Service healthy: `systemctl status ai-trader`
2. Dashboard loads over HTTPS
3. Control Center can submit actions
4. JSONL files are being written to `/srv/ai-trader-data`
5. No transfer permissions disabled (`RESTRICT_FUND_TRANSFERS=true`)
6. Run at least several days with `LIVE_TRADING=false`

## 12. Enable Live Trading

After validation:

1. Set `LIVE_TRADING=true` in `.env`
2. Restart service:

```bash
sudo systemctl restart ai-trader
```

## 13. Operations and Backups

1. EBS snapshots daily for `/srv/ai-trader-data`
2. RDS automated backups enabled
3. Alerts:
   - EC2 CPU/memory/disk
   - service restarts
   - missing portfolio snapshots
4. Update process:

```bash
cd ~/apps/auto_investing
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart ai-trader
```

## 14. Optional Next Step: Direct PostgreSQL Integration

If you want the bot to write directly to PostgreSQL (instead of JSONL files), implement a DB writer layer in:

- `ai_trader_bot/reporting/manager.py`
- `ai_trader_bot/control/center.py`

and keep JSONL as fallback for resilience.

## 15. Alternative Cloud Options

If you do not want AWS:

1. `GCP`: Compute Engine + Cloud SQL (PostgreSQL)
2. `Azure`: VM + Azure Database for PostgreSQL
3. `DigitalOcean`: Droplet + Managed PostgreSQL (simpler UI, smaller scale)

For your use case (continuous trading bot + dashboard + security + growth), AWS is the strongest default.
