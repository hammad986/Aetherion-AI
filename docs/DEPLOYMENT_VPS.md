# Nexora VPS Deployment Guide (Phase W)
> Production Operations & Reliability Testing

This guide details the exact sequence for deploying Nexora to a fresh Linux VPS (Ubuntu 22.04+) for controlled real-world beta testing.

## 1. System Preparation

```bash
# Update and install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv nginx certbot python3-certbot-nginx redis-server -y

# Enable Redis (if using multi-worker)
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

## 2. Code & Environment Setup

```bash
# Create directory
sudo mkdir -p /var/www/nexora
sudo chown -R $USER:$USER /var/www/nexora
cd /var/www/nexora

# Clone repo (assuming you have access)
# git clone <your-repo-url> .

# Setup Python Virtual Environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gevent # Required for SSE production streams

# Configure Secrets
cp .env.example .env
nano .env # Set JWT_SECRET (must be unique 64-char hex), OPENAI_API_KEY, REDIS_URL=redis://localhost:6379/0
```

## 3. Database Permissions

Because gunicorn runs under `www-data`, the database file and its directory must be writable by that user.

```bash
sudo chown -R www-data:www-data /var/www/nexora
sudo chmod 775 /var/www/nexora
```
*(Note: As the deployer, add yourself to the www-data group if you need to manually run `nx_startup_check.py` later: `sudo usermod -aG www-data $USER`)*

## 4. Systemd Service

1. Copy the provided `nexora.service` file to systemd:
```bash
sudo cp nexora.service /etc/systemd/system/
```
2. Set up logging:
```bash
sudo mkdir -p /var/log/nexora
sudo chown www-data:www-data /var/log/nexora
```
3. Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable nexora
sudo systemctl start nexora
sudo systemctl status nexora
```
*(If the service fails to start, `nx_startup_check.py --strict` likely caught an error. Check `journalctl -u nexora -e`)*

## 5. Nginx & SSL

1. Copy the `nginx.conf.example` to sites-available:
```bash
sudo cp nginx.conf.example /etc/nginx/sites-available/nexora
sudo ln -s /etc/nginx/sites-available/nexora /etc/nginx/sites-enabled/
```
2. Edit `/etc/nginx/sites-available/nexora` to replace `nexora.example.com` with your actual domain.
3. Test Nginx configuration: `sudo nginx -t`
4. Reload Nginx: `sudo systemctl reload nginx`
5. Obtain SSL Certificate:
```bash
sudo certbot --nginx -d nexora.example.com
```

**CRITICAL:** Certbot may modify your Nginx config. Open it again and verify that the `/stream/` block still contains:
```nginx
proxy_buffering off;
proxy_read_timeout 3600s;
```
If these are missing, Server-Sent Events (the AI execution stream) will buffer and appear frozen, or disconnect after 60 seconds.

## 6. Daily Operations & Automation

### Automated Backups
To ensure session forensics are never lost during beta:
```bash
sudo crontab -e -u www-data
```
Add this line to run backups every 6 hours safely:
```bash
0 */6 * * * cd /var/www/nexora && /var/www/nexora/venv/bin/python nx_backup.py --silent
```

### Operational Audits
Run these commands locally on the VPS to gather metrics without needing a SaaS dashboard:
```bash
# Check reliability regression trends
./venv/bin/python nx_reliability_trend.py

# Check resource economics & operator trust
./venv/bin/python nx_beta_cohort.py

# Categorize recent failures
./venv/bin/python nx_failure_taxonomy.py
```

### VPS Cold Boot Validation
After everything is running, test a hard reboot to ensure all services recover correctly:
```bash
sudo reboot
```
After restart, visit the URL. The agent should be reachable, and any previously active background sessions should be listed in the session history.
