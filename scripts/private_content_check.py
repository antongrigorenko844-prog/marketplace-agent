"""Read eight requested Market cards; export business data encrypted only."""
import base64, contextlib, datetime, io, json, os, sys, traceback
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
ids = ['0AM325025B', '0AM325587E', '0am325025D', '0am325025f', '0am325025n', '0cw0am325025', '0BH325183 B', 'plita-filtr-v2', '0AM325473']
result = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
log = io.StringIO()
failed = False
with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
    try:
        import yandex_market_client as ym
        biz = ym.get_business_id()
        for kind in ['offer-cards', 'offer-mappings']:
            body = {'offerIds': ids}
            if kind == 'offer-cards': body['withRecommendations'] = True
            status, data = ym._request('POST', f'/v2/businesses/{biz}/{kind}', params={'limit': 100}, json_body=body)
            result[kind] = {'http_status': status, 'body': data}
            assert status == 200 and data.get('status') == 'OK'
    except Exception:
        result['error'] = traceback.format_exc()
        failed = True
result['log'] = log.getvalue()
key = AESGCM.generate_key(bit_length=256)
nonce = os.urandom(12)
pub = serialization.load_pem_public_key((root / 'scripts/black_filter_result_public.pem').read_bytes())
wrapped = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
data = AESGCM(key).encrypt(nonce, json.dumps(result, ensure_ascii=False).encode(), None)
out = root / 'reports/content-check-private.enc'
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps({k: base64.b64encode(v).decode() for k, v in {'key': wrapped, 'nonce': nonce, 'data': data}.items()}))
print('Read-only check finished. Detailed results encrypted for the owner.')
sys.exit(1 if failed else 0)
