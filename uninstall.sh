#!/bin/bash

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit
fi

APP_DIR="/opt/ova-to-ami"

echo "=========================================="
echo "Uninstalling OVA to AMI Web Application"
echo "=========================================="

echo "[*] Stopping and disabling systemd service..."
systemctl stop ova-to-ami 2>/dev/null
systemctl disable ova-to-ami 2>/dev/null
rm -f /etc/systemd/system/ova-to-ami.service
systemctl daemon-reload

echo "[*] Removing Nginx configuration..."
rm -f /etc/nginx/sites-enabled/ova-to-ami
rm -f /etc/nginx/sites-available/ova-to-ami
systemctl restart nginx

echo "[*] Removing application files and virtual environment..."
if [ -d "$APP_DIR" ]; then
    rm -rf "$APP_DIR"
    echo "Removed $APP_DIR"
fi

echo "=========================================="
echo "Uninstallation Complete!"
echo "Note: System packages installed via apt (nginx, python3, etc.) were left intact."
echo "=========================================="
