"""
Cryptographic utility module for encrypting and decrypting sensitive system configuration
(OAuth client secrets, refresh tokens, access tokens) stored in the Google Sheets System_Config sheet.
Compatible with Vercel serverless deployments.
"""
import os
import base64
import hashlib
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Prefix to identify encrypted ciphertext in Google Sheets
CIPHER_PREFIX = "enc::"

# Set of config keys that MUST always be encrypted at rest in System_Config
SENSITIVE_CONFIG_KEYS = {
    'google_user_client_secret',
    'google_user_refresh_token',
    'google_user_access_token'
}

def is_sensitive_key(key: str) -> bool:
    """Returns True if the given config key is sensitive and must be encrypted."""
    if not key:
        return False
    return str(key).strip().lower() in SENSITIVE_CONFIG_KEYS

def get_encryption_key() -> str:
    """
    Returns the encryption key string from environment or config.
    Priority:
    1. CONFIG_ENCRYPTION_KEY (Vercel / .env environment variable)
    2. ENCRYPTION_KEY
    3. Fallback to SECRET_KEY (deterministic key derivation)
    """
    key = os.environ.get('CONFIG_ENCRYPTION_KEY') or os.environ.get('ENCRYPTION_KEY')
    if key and key.strip():
        return key.strip()
    from config import Config
    return getattr(Config, 'SECRET_KEY', 'complaint_system_secure_secret_key_2026_x8f9a2b')

def get_fernet() -> Fernet:
    """
    Constructs a Fernet instance. If the key is not already a valid 32-byte
    urlsafe base64 string, derives a 32-byte key deterministically via SHA-256.
    """
    raw_key = get_encryption_key()
    try:
        decoded = base64.urlsafe_b64decode(raw_key.encode('utf-8'))
        if len(decoded) == 32:
            return Fernet(raw_key.encode('utf-8'))
    except Exception:
        pass

    # Deterministic SHA-256 key derivation
    derived = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode('utf-8')).digest())
    return Fernet(derived)

def encrypt_secret(plain_text: str) -> str:
    """
    Encrypts a sensitive plaintext string (secret or token).
    Returns ciphertext prefixed with 'enc::'.
    If already encrypted, returns without double-encrypting.
    """
    if not plain_text:
        return ''
    str_val = str(plain_text).strip()
    if str_val.startswith(CIPHER_PREFIX):
        return str_val

    try:
        f = get_fernet()
        cipher_bytes = f.encrypt(str_val.encode('utf-8'))
        return CIPHER_PREFIX + cipher_bytes.decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to encrypt secret: {e}")
        raise RuntimeError(f"Encryption failed: {e}")

def decrypt_secret(cipher_text: str) -> str:
    """
    Decrypts a ciphertext string.
    Supports 'enc::<Fernet-token>', raw Fernet tokens starting with 'gAAAAA',
    or returns plaintext unchanged if not encrypted.
    """
    if not cipher_text:
        return ''

    str_val = str(cipher_text).strip()
    token_str = str_val
    if str_val.startswith(CIPHER_PREFIX):
        token_str = str_val[len(CIPHER_PREFIX):]
    elif not str_val.startswith('gAAAAA'):
        # Not encrypted, return as-is
        return str_val

    try:
        f = get_fernet()
        decrypted_bytes = f.decrypt(token_str.encode('utf-8'))
        return decrypted_bytes.decode('utf-8')
    except InvalidToken:
        logger.error("Failed to decrypt secret: InvalidToken (check CONFIG_ENCRYPTION_KEY / SECRET_KEY).")
        return ''
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return ''

def mask_secret(secret: str, show_prefix_chars: int = 6, show_suffix_chars: int = 4) -> str:
    """
    Safely masks a secret string for logs or diagnostics without leaking sensitive data.
    e.g. 'GOCSPX-xxxx...xxxx'
    """
    if not secret:
        return ''
    s = str(secret).strip()
    if len(s) <= (show_prefix_chars + show_suffix_chars):
        return '********'
    return f"{s[:show_prefix_chars]}...{s[-show_suffix_chars:]}"
