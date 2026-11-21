"""Loopback-only JSON API for isolated checkout prototyping.

Run: python -m integrations.ozon_delivery.demo_api
No external dependencies; never publish this service or use in production.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import asdict
from decimal import Decimal
from urllib.parse import urlparse, parse_qs
import json
from .delivery_core import DemoProvider, IntegrationNotReady

provider = DemoProvider()
class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload):
        body=json.dumps(payload,ensure_ascii=False,default=lambda o:str(o) if isinstance(o,Decimal) else o).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        url=urlparse(self.path)
        if url.path=="/api/status":
            return self.respond(200,{"mode":"demo","ozon_connected":False,"orders_enabled":False})
        if url.path=="/api/pvz":
            city=parse_qs(url.query).get("city",[""])[0]
            try: return self.respond(200,{"mode":"demo","points":[asdict(p) for p in provider.list_points(city)]})
            except ValueError as e: return self.respond(400,{"error":str(e)})
        return self.respond(404,{"error":"not_found"})
    def do_POST(self):
        if self.path!="/api/quote":
            return self.respond(501,{"error":"shipment_creation_disabled"})
        try:
            length=int(self.headers.get("Content-Length","0"))
            if not 0<length<=4096: return self.respond(413,{"error":"invalid_body_size"})
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict): raise ValueError("JSON object required")
            quote=provider.quote(data.get("point_id"),data.get("weight_grams"))
            return self.respond(200,{"mode":"demo","quote":asdict(quote),"checkout_enabled":False})
        except (ValueError,TypeError,UnicodeDecodeError,json.JSONDecodeError) as e:
            return self.respond(400,{"error":"invalid_request","details":str(e)[:120]})
if __name__=="__main__":
    print("DEMO ONLY: http://127.0.0.1:8766; orders disabled")
    ThreadingHTTPServer(("127.0.0.1",8766),Handler).serve_forever()
