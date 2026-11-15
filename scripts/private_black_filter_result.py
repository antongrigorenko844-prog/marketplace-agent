"""Run the narrowly scoped operation; export only an encrypted result."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

root = Path(__file__).resolve().parents[1]
result = {}
codes = []
for name in ["zero_black_filter_20261115.py", "read_black_filter_availability.py"]:
    p = subprocess.run([sys.executable, str(root / "scripts" / name)], cwd=root, capture_output=True, text=True)
    result[name] = {"code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    codes.append(p.returncode)
for f in (root / "reports/zero-black-filter-20261115").glob("*.json"):
    result[f.name] = json.loads(f.read_text())
key = AESGCM.generate_key(bit_length=256)
nonce = os.urandom(12)
pub = serialization.load_pem_public_key((root / "scripts/black_filter_result_public.pem").read_bytes())
encrypted_key = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
data = AESGCM(key).encrypt(nonce, json.dumps(result, ensure_ascii=False).encode(), None)
out = root / "reports/black-filter-private.enc"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps({"key": base64.b64encode(encrypted_key).decode(), "nonce": base64.b64encode(nonce).decode(), "data": base64.b64encode(data).decode()}))
print("Operation finished. Detailed result encrypted for the owner's local session.")
sys.exit(1 if any(codes) else 0)
