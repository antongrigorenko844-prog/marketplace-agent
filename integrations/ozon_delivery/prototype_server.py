"""Local-only Ozon Delivery UI prototype. NO live Ozon API or order creation.

Run: python integrations/ozon_delivery/prototype_server.py
Open http://127.0.0.1:8765
Do not expose this demo server publicly or connect it to production checkout.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = (ROOT / "prototype.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/api/status":
            data = json.dumps({
                "mode": "demo",
                "ozon_api_connected": False,
                "pickup_points_available": False,
                "checkout_enabled": False,
                "message": "Ozon API methods and ST100 integration not yet verified"
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
        else:
            self.send_error(404)
            return
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        # Deliberately prevent accidental orders/quotes with unverified endpoints.
        self.send_error(501, "Ozon live API integration is not implemented")

if __name__ == "__main__":
    print("DEMO ONLY: http://127.0.0.1:8765 — no Ozon API calls or real orders")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
