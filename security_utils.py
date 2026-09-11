import hashlib
import urllib.request
import urllib.error

def is_password_pwned(password):
    """
    Checks if a password has been compromised using the HaveIBeenPwned API's k-Anonymity model.
    Returns True if pwned, False otherwise.
    """
    if not password:
        return False
        
    sha1_hash = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()
    prefix = sha1_hash[:5]
    suffix = sha1_hash[5:]
    
    url = f"https://api.pwnedpasswords.com/range/{prefix}"
    
    req = urllib.request.Request(url, headers={'User-Agent': 'HackSmarter-OVA-App'})
    
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                text = response.read().decode('utf-8')
                for line in text.splitlines():
                    hash_suffix, count = line.split(':')
                    if hash_suffix == suffix:
                        return True
    except urllib.error.URLError:
        # If the API is down or unreachable, fail open so users can still change passwords
        pass
        
    return False
