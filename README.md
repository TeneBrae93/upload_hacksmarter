# Hack Smarter OVA to AMI Uploader

This web application facilitates the upload of Virtual Machine OVA files directly to Amazon S3 and automates their conversion into Amazon EC2 AMIs using the AWS VM Import/Export service. It is designed to handle multi-gigabyte file uploads securely and reliably.

<img width="1642" height="736" alt="image" src="https://github.com/user-attachments/assets/86792f9d-22e0-411b-9451-d00cbcd41634" />

## Architecture

The application leverages a robust **S3 Multipart Upload** architecture to handle virtually unlimited file sizes (bypassing the strict 5GB limit imposed by standard S3 PUT requests).

Instead of routing massive OVA files through the web server, the client's browser uses vanilla Javascript to mathematically slice the file into 50MB chunks. The Python backend securely generates a presigned S3 URL for *each* specific chunk. The browser uploads these chunks directly to AWS, bypassing server-side memory limits and bandwidth bottlenecks. Once all chunks are uploaded, AWS natively re-assembles them into the original massive OVA file. Finally, the backend orchestrates the AWS VM Import process and shares the resulting AMI with the designated target AWS account.

## Requirements

- Ubuntu 22.04 or 24.04 (Recommended)
- Root access
- AWS Access Key and Secret Key with permissions to manage S3, IAM roles, and EC2 Image Imports
- A target AWS Account ID to share the generated AMIs with. Note: By default, this is hardcoded to target the CourseStack AWS account.

## Installation

The application includes an automated deployment script. The script installs all necessary system packages, configures a Python virtual environment, sets up a systemd service running Gunicorn, and configures Nginx as a reverse proxy.

1. Clone or download this repository to the target server.
2. Execute the installation script as root:
   ```bash
   sudo ./install.sh
   ```
3. The script will prompt you for your AWS credentials, administrative user credentials, and an optional domain name. 
4. If a domain name is provided and DNS is properly configured, the script can automatically provision an SSL/TLS certificate via Certbot (Let's Encrypt).

## Updating

To pull the latest code changes and apply them without disrupting your existing database or environment configuration, run the update script from the repository directory:

```bash
sudo ./update.sh
```

## Uninstallation

To completely remove the application, service files, and Nginx configurations from the server, execute:

```bash
sudo ./uninstall.sh
```
*Note: This will not remove system packages installed by `apt-get` during the initial setup.*

## Security Features

- **Role-Based Access Control (RBAC)**: Two distinct roles are enforced—**User** and **Admin**. Users are strictly isolated to viewing and managing only their own OVA uploads. Admins have global visibility over all tasks and exclusive access to the Admin Panel for user lifecycle management.
- **Multi-Factor Authentication (MFA)**: Enforced globally via Time-based One-Time Passwords (TOTP). Furthermore, high-risk administrative actions (e.g., promoting a user to Admin, deleting an account) and account settings (e.g., updating passwords) require re-verification of the MFA token with a strict timestamp window to protect against session hijacking.
- **Injection Attack Defenses (SSTI & XSS)**: The frontend templates utilize Jinja2 with strict HTML auto-escaping enabled by default, rendering the application immune to Server-Side Template Injection (SSTI) and Cross-Site Scripting (XSS). User inputs and uploaded filenames are strictly sanitized via Werkzeug's `secure_filename()` before any processing.
- **Insecure Direct Object Reference (IDOR) Protection**: All backend API endpoints handling file deletion or status checks enforce rigid ownership checks against the authenticated session.
- **CSRF Protection**: Flask-WTF CSRF tokens are cryptographically required across all state-changing endpoints and internal API calls.
- **Rate Limiting**: Strict rate limiting (e.g., 200/day, 50/hour) is applied globally, with even tighter restrictions on authentication endpoints to mitigate brute-force attempts. Internal multipart upload endpoints are explicitly exempt to allow massive file chunk streams.
- **Password Policies**: Requires initial password resets for all newly provisioned users. Passwords strictly adhere to NIST SP 800-63B guidelines (minimum 8 characters, maximum 64 characters, avoiding arbitrary complexity rules).
- **Breached Password Protection**: Integrates with the HaveIBeenPwned API to block the use of compromised passwords across all password creation forms. This is implemented via the cryptographic k-Anonymity model, transmitting only the first 5 characters of the password's SHA-1 hash to ensure complete privacy.

## Disclosure

This codebase was developed in collaboration with Google Antigravity, an advanced agentic coding assistant.

## Cost Management

To prevent invisible AWS storage costs from accumulating due to orphaned 50MB chunks (e.g., if a user closes their laptop mid-upload), the application automatically configures an **S3 Lifecycle Rule** on the temporary buckets using Boto3. This rule is hardcoded to "abort incomplete multipart uploads" after 48 hours, ensuring that any fragmented files are automatically purged from AWS without requiring manual administrative intervention.
