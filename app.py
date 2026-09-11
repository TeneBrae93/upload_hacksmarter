import os
import io
import qrcode
import pyotp
import uuid
import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError, OperationalError

from models import db, User, UploadTask
from aws_utils import (
    get_clients, create_temp_bucket, generate_presigned_post,
    create_iam_role_and_policy, start_import_task, check_import_status,
    share_resources, cleanup_bucket,
    create_multipart_upload, generate_presigned_part_url, complete_multipart_upload
)

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Secure Cookies
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('HTTPS_ENABLED') == 'true'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db.init_app(app)
csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# Rate limiter to prevent bruteforce
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    try:
        db.create_all()
    except OperationalError:
        pass  # Another worker is already creating the tables
    # Create default Admin user if it doesn't exist and env vars are set
    admin_user = os.environ.get('ADMIN_USERNAME')
    admin_pass = os.environ.get('ADMIN_PASSWORD')
    
    if admin_user and admin_pass:
        try:
            if not User.query.filter_by(username=admin_user).first():
                admin = User(
                    username=admin_user,
                    password_hash=generate_password_hash(admin_pass),
                    is_admin=True,
                    must_change_password=True
                )
                db.session.add(admin)
                db.session.commit()
        except IntegrityError:
            db.session.rollback()

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute") # Extra strict on login
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            from flask import session
            if getattr(user, 'must_change_password', False):
                session['require_password_change_user_id'] = user.id
                return redirect(url_for('change_password'))
                
            session['pre_mfa_user_id'] = user.id
            if not user.mfa_enabled:
                return redirect(url_for('setup_mfa'))
            else:
                return redirect(url_for('verify_mfa'))
        else:
            flash('Invalid username or password', 'danger')
            
    return render_template('login.html')

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    from flask import session
    user_id = session.get('require_password_change_user_id')
    if not user_id:
        return redirect(url_for('login'))
        
    user = User.query.get(user_id)
    
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not new_password or new_password != confirm_password:
            flash('Passwords do not match', 'danger')
        elif len(new_password) < 8:
            flash('Password must be at least 8 characters long', 'danger')
        else:
            user.password_hash = generate_password_hash(new_password)
            user.must_change_password = False
            db.session.commit()
            
            session.pop('require_password_change_user_id', None)
            session['pre_mfa_user_id'] = user.id
            flash('Password updated successfully. Please continue to MFA.', 'success')
            
            if not user.mfa_enabled:
                return redirect(url_for('setup_mfa'))
            else:
                return redirect(url_for('verify_mfa'))
                
    return render_template('change_password.html')

@app.route('/update_password', methods=['GET', 'POST'])
@login_required
@limiter.limit("5 per minute")
def update_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        mfa_token = request.form.get('mfa_token')
        
        if not check_password_hash(current_user.password_hash, current_password):
            flash('Incorrect current password.', 'danger')
        elif not new_password or new_password != confirm_password:
            flash('New passwords do not match.', 'danger')
        elif len(new_password) < 8:
            flash('New password must be at least 8 characters long.', 'danger')
        elif not pyotp.TOTP(current_user.mfa_secret).verify(mfa_token):
            flash('Invalid MFA code.', 'danger')
        else:
            current_user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash('Your password has been securely updated.', 'success')
            return redirect(url_for('dashboard'))
            
    return render_template('update_password.html')

@app.route('/setup_mfa', methods=['GET', 'POST'])
def setup_mfa():
    from flask import session
    user_id = session.get('pre_mfa_user_id')
    if not user_id:
        return redirect(url_for('login'))
        
    user = User.query.get(user_id)
    if user.mfa_enabled:
        return redirect(url_for('verify_mfa'))
        
    if request.method == 'POST':
        token = request.form.get('token')
        totp = pyotp.TOTP(session.get('mfa_secret'))
        if totp.verify(token):
            user.mfa_secret = session.get('mfa_secret')
            user.mfa_enabled = True
            db.session.commit()
            login_user(user)
            session.pop('pre_mfa_user_id', None)
            session.pop('mfa_secret', None)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid MFA Token', 'danger')

    # Generate new secret only if not already in session
    secret = session.get('mfa_secret')
    if not secret:
        secret = pyotp.random_base32()
        session['mfa_secret'] = secret
    
    # We no longer pass the URI to the template, the frontend will call /qr_code which securely generates it
    return render_template('mfa_setup.html', secret=secret)

@app.route('/qr_code')
@limiter.limit("10 per minute")
def qr_code():
    from flask import session
    user_id = session.get('pre_mfa_user_id')
    if not user_id:
        return "Unauthorized", 403
        
    user = User.query.get(user_id)
    secret = session.get('mfa_secret')
    if not secret:
        return "No secret generated", 400
        
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=user.username, issuer_name="Hack Smarter OVA App")
    
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/verify_mfa', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def verify_mfa():
    from flask import session
    user_id = session.get('pre_mfa_user_id')
    if not user_id:
        return redirect(url_for('login'))
        
    user = User.query.get(user_id)
    if not user.mfa_enabled:
        return redirect(url_for('setup_mfa'))
        
    if request.method == 'POST':
        token = request.form.get('token')
        totp = pyotp.TOTP(user.mfa_secret)
        if totp.verify(token):
            login_user(user)
            session.pop('pre_mfa_user_id', None)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid MFA Token', 'danger')
            
    return render_template('mfa_verify.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        uploads = UploadTask.query.order_by(UploadTask.created_at.desc()).all()
    else:
        uploads = UploadTask.query.filter_by(user_id=current_user.id).order_by(UploadTask.created_at.desc()).all()
    return render_template('dashboard.html', uploads=uploads)

@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        flash('Unauthorized', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.all()
    return render_template('admin.html', users=users)

@app.route('/admin/create_user', methods=['POST'])
@login_required
def create_user():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    username = request.form.get('username')
    password = request.form.get('password')
    
    if User.query.filter_by(username=username).first():
        flash('Username already exists', 'danger')
    else:
        new_user = User(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=False
        )
        db.session.add(new_user)
        db.session.commit()
        flash('User created successfully', 'success')
        
    return redirect(url_for('admin'))

# --- API Routes for Upload Flow ---

@app.route('/api/upload/multipart/create', methods=['POST'])
@login_required
def multipart_create():
    try:
        filename = secure_filename(request.json.get('filename'))
        s3_client, iam_client, ec2_client = get_clients()
        
        bucket_name = create_temp_bucket(s3_client)
        s3_key = f"uploads/{uuid.uuid4()}-{filename}"
        
        create_iam_role_and_policy(iam_client, bucket_name)
        
        upload_id = create_multipart_upload(s3_client, bucket_name, s3_key)
        
        task = UploadTask(
            filename=filename,
            s3_bucket=bucket_name,
            user_id=current_user.id
        )
        db.session.add(task)
        db.session.commit()
        
        return jsonify({
            'upload_id': upload_id,
            'task_id': task.id,
            's3_key': s3_key,
            'bucket_name': bucket_name
        })
    except Exception as e:
        print(f"Error creating multipart: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload/multipart/sign', methods=['POST'])
@login_required
def multipart_sign():
    try:
        bucket_name = request.json.get('bucket_name')
        s3_key = request.json.get('s3_key')
        upload_id = request.json.get('upload_id')
        part_number = request.json.get('part_number')
        
        s3_client, _, _ = get_clients()
        url = generate_presigned_part_url(s3_client, bucket_name, s3_key, upload_id, part_number)
        
        return jsonify({'url': url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload/multipart/complete', methods=['POST'])
@login_required
def multipart_complete():
    try:
        bucket_name = request.json.get('bucket_name')
        s3_key = request.json.get('s3_key')
        upload_id = request.json.get('upload_id')
        parts = request.json.get('parts')
        
        s3_client, _, _ = get_clients()
        complete_multipart_upload(s3_client, bucket_name, s3_key, upload_id, parts)
        
        return jsonify({'message': 'Upload complete'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload/start', methods=['POST'])
@login_required
def start_import():
    try:
        task_id = request.json.get('task_id')
        s3_key = request.json.get('s3_key')
        bucket_name = request.json.get('bucket_name')
        
        task = UploadTask.query.get(task_id)
        if not task or task.user_id != current_user.id:
            return jsonify({'error': 'Task not found'}), 404
            
        s3_client, _, ec2_client = get_clients()
        
        # Verify file is in S3
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        except Exception:
            return jsonify({'error': 'File not found in S3'}), 400
            
        aws_task_id = start_import_task(ec2_client, bucket_name, s3_key)
        
        task.aws_task_id = aws_task_id
        task.status = 'importing'
        db.session.commit()
        
        return jsonify({'message': 'Import started', 'aws_task_id': aws_task_id})
    except Exception as e:
        print(f"Error starting import: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload/status/<int:task_id>', methods=['GET'])
@login_required
def upload_status(task_id):
    task = UploadTask.query.get(task_id)
    if not task or (task.user_id != current_user.id and not current_user.is_admin):
        return jsonify({'error': 'Not found'}), 404
        
    if task.status in ['completed', 'failed', 'deleted']:
        return jsonify({'status': task.status, 'ami_id': task.ami_id})
        
    if not task.aws_task_id:
         return jsonify({'status': task.status})

    try:
        _, _, ec2_client = get_clients()
        aws_task = check_import_status(ec2_client, task.aws_task_id)
        aws_status = aws_task['Status']
        progress = aws_task.get('Progress', '0')
        status_msg = aws_task.get('StatusMessage', '')
        
        if aws_status == 'completed':
            task.ami_id = aws_task.get('ImageId')
            task.status = 'completed'
            db.session.commit()
            
            # Share and cleanup in background
            try:
                share_resources(ec2_client, task.ami_id)
            except Exception as share_e:
                print(f"Share error: {share_e}")
                
            s3_client, _, _ = get_clients()
            # Try to infer s3_key from aws_task
            try:
                disk = aws_task.get('SnapshotDetails', [{}])[0].get('DiskImageSize') # Not the easiest way
                # It's better if we stored s3_key in DB. Let's just find anything in the bucket and delete it.
                objs = s3_client.list_objects_v2(Bucket=task.s3_bucket)
                for obj in objs.get('Contents', []):
                    s3_client.delete_object(Bucket=task.s3_bucket, Key=obj['Key'])
                s3_client.delete_bucket(Bucket=task.s3_bucket)
            except Exception as cleanup_e:
                 print(f"Cleanup error: {cleanup_e}")
                 
        elif aws_status in ['deleted', 'deleting', 'failed']:
            task.status = 'failed'
            db.session.commit()
            
        return jsonify({
            'status': task.status,
            'aws_status': aws_status,
            'progress': progress,
            'status_message': status_msg,
            'ami_id': task.ami_id
        })
    except Exception as e:
        print(f"Error checking status: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
