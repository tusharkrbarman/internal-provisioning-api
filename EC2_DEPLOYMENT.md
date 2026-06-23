# EC2 Deployment

This guide deploys the middleware API on an Ubuntu EC2 instance as a systemd service.

## 1. Launch EC2

Recommended demo setup:

```text
AMI: Ubuntu Server 24.04 LTS or 22.04 LTS
Instance type: t2.micro or t3.micro
Storage: 8-16 GB
Inbound security group:
  SSH 22 from your IP
  TCP 8080 from your IP or Jenkins VM IP
```

If Jenkins is running in another VM, allow port `8080` from that VM's public IP or private subnet.

## 2. SSH Into EC2

```bash
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>
```

## 3. Install Dependencies

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip curl
```

## 4. Clone The Repo

```bash
sudo git clone https://github.com/tusharkrbarman/internal-provisioning-api.git /opt/internal-provisioning-api
sudo chown -R ubuntu:ubuntu /opt/internal-provisioning-api
cd /opt/internal-provisioning-api
```

## 5. Create Python Virtual Environment

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 6. Configure Provider URLs

Create the environment file:

```bash
sudo nano /etc/internal-provisioning-api.env
```

Paste:

```text
ONECLOUD_BASE_URL=https://dummy-onecloud-api.onrender.com
GTAX_BASE_URL=https://dummy-gtax-api.onrender.com
PROVISION_POLL_INTERVAL_SECONDS=2
PROVISION_TIMEOUT_SECONDS=300
```

Replace the URLs with your actual provider API URLs if Render assigned different names.

## 7. Install systemd Service

```bash
sudo cp deploy/ec2/internal-provisioning-api.service /etc/systemd/system/internal-provisioning-api.service
sudo systemctl daemon-reload
sudo systemctl enable internal-provisioning-api
sudo systemctl start internal-provisioning-api
```

Check status:

```bash
sudo systemctl status internal-provisioning-api --no-pager
```

View logs:

```bash
journalctl -u internal-provisioning-api -f
```

## 8. Test From EC2

```bash
curl http://localhost:8080/health
curl http://localhost:8080/scenarios
```

## 9. Test From Your Machine Or Jenkins VM

Use:

```text
http://<EC2_PUBLIC_IP>:8080/health
```

Example:

```bash
curl http://<EC2_PUBLIC_IP>:8080/health
```

If this fails:

- Confirm the EC2 security group allows inbound TCP `8080`.
- Confirm Ubuntu firewall is not blocking it: `sudo ufw status`.
- Confirm the service is listening: `ss -ltnp | grep 8080`.

## 10. Jenkins Configuration

In Jenkins pipeline parameters, set:

```text
PROVISION_API=http://<EC2_PUBLIC_IP>:8080
```

The Jenkinsfile will call:

```text
POST /provision
GET  /provision/{request_id}/status
POST /reservations/{reservation_id}/release
```

## 11. Updating The Service

```bash
cd /opt/internal-provisioning-api
git pull
. .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart internal-provisioning-api
```

## Optional: Nginx Reverse Proxy

For a demo, direct port `8080` is enough. If you want a cleaner URL on port `80`, install Nginx and proxy to Uvicorn:

```bash
sudo apt install -y nginx
sudo nano /etc/nginx/sites-available/internal-provisioning-api
```

Paste:

```nginx
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/internal-provisioning-api /etc/nginx/sites-enabled/internal-provisioning-api
sudo nginx -t
sudo systemctl reload nginx
```

Then Jenkins can use:

```text
PROVISION_API=http://<EC2_PUBLIC_IP>
```
