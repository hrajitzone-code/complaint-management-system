import os
import sys

# Ensure root directory is on Python path so app and services modules are found
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app import app

# Vercel Python serverless WSGI entrypoint
# The 'app' variable is automatically detected by @vercel/python
