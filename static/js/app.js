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
    document.getElementById('status-text').textContent = 'Preparing S3 Multipart Upload...';

    try {
        // 1. Create Multipart Upload
        const prepareRes = await fetch('/api/upload/multipart/create', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ filename: file.name })
        });
        
        if (!prepareRes.ok) throw new Error('Failed to create multipart upload');
        const prepareData = await prepareRes.json();
        
        // 2. Upload Chunks
        document.getElementById('status-text').textContent = 'Uploading to S3 (Multipart)...';
        const parts = await uploadChunks(file, prepareData);
        
        // 3. Complete Multipart Upload
        document.getElementById('status-text').textContent = 'Finalizing Upload on S3...';
        const completeRes = await fetch('/api/upload/multipart/complete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                upload_id: prepareData.upload_id,
                s3_key: prepareData.s3_key,
                bucket_name: prepareData.bucket_name,
                parts: parts
            })
        });
        if (!completeRes.ok) throw new Error('Failed to complete upload');
        
        // 4. Start AWS Import Task
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
        
        // 5. Poll status
        pollStatus(prepareData.task_id);
        
    } catch (err) {
        console.error(err);
        document.getElementById('status-text').textContent = `Error: ${err.message}`;
        document.getElementById('status-text').style.color = 'var(--danger)';
    }
}

async function uploadChunks(file, uploadContext) {
    const CHUNK_SIZE = 50 * 1024 * 1024; // 50MB
    const numChunks = Math.ceil(file.size / CHUNK_SIZE);
    let parts = [];
    let uploadedBytes = 0;

    for (let i = 0; i < numChunks; i++) {
        const partNumber = i + 1;
        const start = i * CHUNK_SIZE;
        const end = Math.min(start + CHUNK_SIZE, file.size);
        const chunk = file.slice(start, end);

        // Get presigned URL for this part
        const signRes = await fetch('/api/upload/multipart/sign', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                upload_id: uploadContext.upload_id,
                s3_key: uploadContext.s3_key,
                bucket_name: uploadContext.bucket_name,
                part_number: partNumber
            })
        });
        if (!signRes.ok) throw new Error(`Failed to sign chunk ${partNumber}`);
        const { url } = await signRes.json();

        // Upload chunk
        const etag = await uploadChunk(url, chunk, (progress) => {
            if (progress.lengthComputable) {
                const totalUploaded = uploadedBytes + progress.loaded;
                const percent = Math.round((totalUploaded / file.size) * 100);
                document.getElementById('progress-bar').style.width = percent + '%';
                document.getElementById('progress-percent').textContent = percent + '%';
            }
        });

        uploadedBytes += chunk.size;
        parts.push({ PartNumber: partNumber, ETag: etag });
    }
    return parts;
}

function uploadChunk(url, chunk, onProgress) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', url, true);

        xhr.upload.onprogress = onProgress;

        xhr.onload = () => {
            if (xhr.status === 200) {
                const etag = xhr.getResponseHeader('ETag');
                resolve(etag);
            } else {
                let errorMsg = `Chunk upload failed with status ${xhr.status}`;
                if (xhr.responseText) {
                    try {
                        const parser = new DOMParser();
                        const xmlDoc = parser.parseFromString(xhr.responseText, "text/xml");
                        const code = xmlDoc.getElementsByTagName("Code")[0]?.childNodes[0]?.nodeValue;
                        const message = xmlDoc.getElementsByTagName("Message")[0]?.childNodes[0]?.nodeValue;
                        if (code && message) errorMsg += ` - ${code}: ${message}`;
                    } catch (e) {}
                }
                reject(new Error(errorMsg));
            }
        };

        xhr.onerror = () => reject(new Error('Network error during chunk upload'));
        xhr.send(chunk);
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
                const progress = data.progress || '0';
                document.getElementById('status-text').textContent = `AWS Importing: ${data.aws_status || 'Processing'}`;
                document.getElementById('progress-bar').style.width = progress + '%';
                document.getElementById('progress-percent').textContent = progress + '%';
                // Optional pulsating effect
                document.getElementById('progress-bar').style.animation = 'pulse 2s infinite';
            }
            
        } catch (err) {
            console.error('Polling error', err);
        }
    }, 15000); // Check every 15 seconds
}

async function deleteUpload(taskId) {
    if (!confirm('Are you sure you want to delete this upload? This will completely deregister the AMI and delete all AWS snapshots and data.')) {
        return;
    }
    
    try {
        const res = await fetch(`/api/upload/${taskId}`, {
            method: 'DELETE',
            headers: {
                'X-CSRFToken': csrfToken
            }
        });
        
        if (res.ok) {
            const row = document.getElementById(`upload-row-${taskId}`);
            if (row) row.remove();
        } else {
            const data = await res.json();
            alert(`Error: ${data.error || 'Failed to delete upload'}`);
        }
    } catch (err) {
        alert('Network error while deleting upload.');
    }
}

function resumeTracking(taskId, filename) {
    document.getElementById('drop-zone').style.display = 'none';
    document.getElementById('progress-container').style.display = 'block';
    document.getElementById('upload-filename').textContent = filename;
    document.getElementById('status-text').textContent = 'Resuming AWS tracking...';
    document.getElementById('progress-bar').style.width = '0%';
    document.getElementById('progress-percent').textContent = '';
    document.getElementById('progress-bar').style.background = 'var(--primary)';
    
    window.scrollTo({ top: 0, behavior: 'smooth' });
    
    pollStatus(taskId);
}
