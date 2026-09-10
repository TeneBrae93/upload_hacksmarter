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
echo "Updating OVA to AMI Web Application"
echo "=========================================="

echo "[*] Pulling latest changes from git..."
# If this is a git repository, pull the latest changes
if [ -d ".git" ]; then
    git pull
else
    echo "[!] Not a git repository. Assuming files are already updated locally."
fi

echo "[*] Copying updated files to $APP_DIR..."
# Copy files, overwriting old ones, but ignoring .git and preserving existing .env/instance files in the destination
rsync -a --exclude='.git' --exclude='instance' --exclude='venv' --exclude='.env' ./ $APP_DIR/

echo "[*] Ensuring correct ownership..."
chown -R $USER:$USER $APP_DIR

echo "[*] Updating Python dependencies..."
sudo -u $USER bash -c "$VENV_DIR/bin/pip install -r $APP_DIR/requirements.txt"

echo "[*] Restarting application service..."
systemctl restart ova-to-ami
systemctl restart nginx

echo "=========================================="
echo "Update Complete!"
echo "Your configuration (.env) and database (instance/) were preserved."
echo "=========================================="
