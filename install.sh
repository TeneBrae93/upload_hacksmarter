#!/bin/bash

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit
fi

APP_DIR="/opt/ova-to-ami"
VENV_DIR="$APP_DIR/venv"
USER="www-data"

echo "=========================================="
echo "Installing OVA to AMI Web Application"
echo "=========================================="

# 1. Update and install system dependencies
echo "[*] Installing system dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv nginx python3-dev build-essential libjpeg-dev zlib1g-dev

# 2. Setup Application Directory
echo "[*] Setting up application directory..."
mkdir -p $APP_DIR
mkdir -p $APP_DIR/instance
cp -r ./* $APP_DIR/
chown -R $USER:$USER $APP_DIR

# 3. Prompt for AWS Credentials
echo "=========================================="
echo "Please provide your AWS Credentials for S3 and EC2 AMI Import."
echo "These will be stored securely in $APP_DIR/.env"
echo "=========================================="

read -p "AWS Access Key ID: " AWS_ACCESS_KEY_ID
read -p "AWS Secret Access Key: " AWS_SECRET_ACCESS_KEY
read -p "AWS Default Region (e.g., us-east-1): " AWS_DEFAULT_REGION

echo "=========================================="
echo "Admin Configuration"
echo "=========================================="
read -p "Enter your Admin Username: " ADMIN_USERNAME
read -s -p "Enter your Admin Password: " ADMIN_PASSWORD
echo ""

echo "=========================================="
echo "Domain Configuration"
echo "=========================================="
read -p "Enter your Domain Name (e.g., portal.hacksmarter.com) or leave blank to use IP: " DOMAIN_NAME
DOMAIN_NAME=${DOMAIN_NAME:-_}

cat << EOF > $APP_DIR/.env
AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY
AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION
ADMIN_USERNAME=$ADMIN_USERNAME
ADMIN_PASSWORD=$ADMIN_PASSWORD
SECRET_KEY=$(python3 -c 'import os; print(os.urandom(24).hex())')
FLASK_ENV=production
EOF

chown $USER:$USER $APP_DIR/.env
chmod 600 $APP_DIR/.env

# 4. Setup Python Virtual Environment
echo "[*] Setting up Python virtual environment..."
sudo -u $USER bash -c "python3 -m venv $VENV_DIR"
sudo -u $USER bash -c "$VENV_DIR/bin/pip install -r $APP_DIR/requirements.txt"

# 5. Create Systemd Service for Gunicorn
echo "[*] Configuring Systemd Service..."
cat << EOF > /etc/systemd/system/ova-to-ami.service
[Unit]
Description=Gunicorn instance to serve OVA to AMI App
After=network.target

[Service]
User=$USER
Group=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment="PATH=$VENV_DIR/bin"
ExecStart=$VENV_DIR/bin/gunicorn --workers 3 --bind unix:app.sock -m 007 wsgi:app

[Install]
WantedBy=multi-user.target
EOF

# 6. Create WSGI entry point
echo "[*] Creating WSGI entry point..."
cat << EOF > $APP_DIR/wsgi.py
from app import app

if __name__ == "__main__":
    app.run()
EOF
chown $USER:$USER $APP_DIR/wsgi.py

# 7. Configure Nginx
echo "[*] Configuring Nginx..."
cat << EOF > /etc/nginx/sites-available/ova-to-ami
server {
    listen 80;
    server_name $DOMAIN_NAME;

    # Increase max upload size (though direct to S3 mostly bypasses this, good to have)
    client_max_body_size 100M;

    location / {
        include proxy_params;
        proxy_pass http://unix:$APP_DIR/app.sock;
    }
}
EOF

ln -sf /etc/nginx/sites-available/ova-to-ami /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# 8. Start and Enable Services
echo "[*] Starting services..."
systemctl daemon-reload
systemctl start ova-to-ami
systemctl enable ova-to-ami
systemctl restart nginx

echo "=========================================="
echo "Installation Complete!"
echo "The application is now running. Please ensure your Droplet's firewall allows traffic on port 80/443."

SERVER_IP=$(curl -s http://checkip.amazonaws.com || echo "<your_server_ip>")

if [ "$DOMAIN_NAME" != "_" ]; then
    echo "=========================================="
    echo "DNS Configuration Required"
    echo "=========================================="
    echo "Please go to your domain registrar or DNS provider and add the following record:"
    echo "Type: A"
    echo "Name: (the subdomain part, e.g. 'portal' for portal.hacksmarter.com, or '@' for root domain)"
    echo "Value: $SERVER_IP"
    echo ""
    echo "Once DNS propagates, access the app at http://$DOMAIN_NAME/"
else
    echo "Access the app at http://$SERVER_IP/"
fi
echo "Default admin user is '$ADMIN_USERNAME' with the password you provided."
echo "=========================================="
