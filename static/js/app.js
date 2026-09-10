const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

function handleFileSelect(event) {
    const file = event.target.files[0];
    if (file) {
        if (!file.name.endsWith('.ova')) {
            alert('Please select a valid .ova file.');
            return;
        }
        startUploadProcess(file);
    }
}

// Drag and drop logic
const dropZone = document.getElementById('drop-zone');
if (dropZone) {
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false);
    });

    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const file = dt.files[0];
        if (file) {
            if (!file.name.endsWith('.ova')) {
                alert('Please select a valid .ova file.');
                return;
            }
            startUploadProcess(file);
        }
    });
}

async function startUploadProcess(file) {
    document.getElementById('drop-zone').style.display = 'none';
    document.getElementById('progress-container').style.display = 'block';
    document.getElementById('upload-filename').textContent = file.name;
    document.getElementById('status-text').textContent = 'Preparing S3 Upload...';

    try {
        // 1. Prepare upload
        const prepareRes = await fetch('/api/upload/prepare', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ filename: file.name })
        });
        
        if (!prepareRes.ok) throw new Error('Failed to prepare upload');
        const prepareData = await prepareRes.json();
        
        // 2. Direct S3 Upload
        document.getElementById('status-text').textContent = 'Uploading to S3 directly (this might take a while)...';
        await uploadToS3(file, prepareData.presigned_data);
        
        // 3. Start AWS Import Task
        document.getElementById('status-text').textContent = 'Starting AMI Import...';
        const startRes = await fetch('/api/upload/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                task_id: prepareData.task_id,
                s3_key: prepareData.s3_key,
                bucket_name: prepareData.bucket_name
            })
        });
        
        if (!startRes.ok) throw new Error('Failed to start import task');
        
        // 4. Poll status
        pollStatus(prepareData.task_id);
        
    } catch (err) {
        console.error(err);
        document.getElementById('status-text').textContent = `Error: ${err.message}`;
        document.getElementById('status-text').style.color = 'var(--danger)';
    }
}

function uploadToS3(file, presignedData) {
    return new Promise((resolve, reject) => {
        const formData = new FormData();
        
        // Add presigned fields
        Object.keys(presignedData.fields).forEach(key => {
            formData.append(key, presignedData.fields[key]);
        });
        
        // Add file (must be last)
        formData.append('file', file);
        
        const xhr = new XMLHttpRequest();
        xhr.open('POST', presignedData.url, true);
        
        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                document.getElementById('progress-bar').style.width = percent + '%';
                document.getElementById('progress-percent').textContent = percent + '%';
            }
        };
        
        xhr.onload = () => {
            if (xhr.status === 204 || xhr.status === 200) {
                resolve();
            } else {
                reject(new Error(`S3 Upload failed with status ${xhr.status}`));
            }
        };
        
        xhr.onerror = () => reject(new Error('S3 Upload network error'));
        
        xhr.send(formData);
    });
}

function pollStatus(taskId) {
    const interval = setInterval(async () => {
        try {
            const res = await fetch(`/api/upload/status/${taskId}`);
            const data = await res.json();
            
            if (data.status === 'completed') {
                clearInterval(interval);
                document.getElementById('status-text').textContent = `Success! AMI ID: ${data.ami_id}`;
                document.getElementById('progress-bar').style.background = 'var(--success)';
                setTimeout(() => window.location.reload(), 3000);
            } else if (data.status === 'failed') {
                clearInterval(interval);
                document.getElementById('status-text').textContent = `Import Failed: ${data.status_message || ''}`;
                document.getElementById('status-text').style.color = 'var(--danger)';
                document.getElementById('progress-bar').style.background = 'var(--danger)';
            } else if (data.status === 'importing') {
                document.getElementById('status-text').textContent = `AWS Importing: ${data.aws_status || 'Processing'} (${data.progress || '0'}%)`;
                document.getElementById('progress-bar').style.width = '100%';
                document.getElementById('progress-percent').textContent = '';
                // Optional pulsating effect
                document.getElementById('progress-bar').style.animation = 'pulse 2s infinite';
            }
            
        } catch (err) {
            console.error('Polling error', err);
        }
    }, 15000); // Check every 15 seconds
}
