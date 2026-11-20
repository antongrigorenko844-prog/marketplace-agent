"""Avito stock transport restricted to the six independently matched LEOPARTS listings."""
from http.client import HTTPException
import json
import os
from threading import Lock
from types import MappingProxyType
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


LISTINGS = MappingProxyType({
    "0am325025x": 8252041186,
    "0am325066ae": 8385926244,
    "plita-filtr-orig": 8304712062,
    "plita-pylniki-salniki": 8394895937,
    "plita-pylniki-salniki-orig": 8394697530,
    "plita-pylniki-salniki-orig-v2": 8386090647,
})
_SENT_IDS = set()
_SEND_LOCK = Lock()
_CODES = frozenset({
    "unknown_listing", "missing_credentials", "authentication_failed", "account_unconfirmed",
    "unexpected_status", "unexpected_response_size", "response_schema", "transport_error",
    "unexpected_autoload_response", "duplicate_autoload_row", "invalid_autoload_identifier",
    "historical_link_changed", "listing_linked_to_another_sku", "listing_not_active",
    "reverse_link_conflicts", "stock_identity_unconfirmed", "stock_flags_unconfirmed",
    "inconsistent_stock", "invalid_quantity", "duplicate_write_prevented",
    "receipt_identity_unconfirmed", "item_rejected",
})


class StockError(Exception):
    """Expose only stable internal codes, never supplier bodies or credentials."""
    def __init__(self, code):
        http_code = (isinstance(code, str) and len(code)==8 and code.startswith("http_")
                     and code[5:].isascii() and code[5:].isdigit() and 100<=int(code[5:])<=599)
        self.code = code if isinstance(code,str) and (code in _CODES or http_code) else "stock_error"
        super().__init__(self.code)


def _require(truth, code):
    if not truth:
        raise StockError(code)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _records(reply, key):
    _require(isinstance(reply,dict), "response_schema")
    entries = reply.get(key)
    _require(isinstance(entries,list) and all(isinstance(row,dict) for row in entries), "response_schema")
    return entries


def _listing(sku):
    _require(isinstance(sku,str) and sku in LISTINGS, "unknown_listing")
    return LISTINGS[sku]


class StockClient:
    def __init__(self):
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._token = None
        client_id,secret = os.getenv("AVITO_CLIENT_ID"),os.getenv("AVITO_CLIENT_SECRET")
        _require(client_id and secret,"missing_credentials")
        token = self._request("POST","/token",{"grant_type":"client_credentials",
                              "client_id":client_id,"client_secret":secret},form=True)
        self._token = token.get("access_token")
        _require(isinstance(self._token,str) and bool(self._token),"authentication_failed")
        self._account = self._request("GET","/core/v1/accounts/self").get("id")
        _require(type(self._account) is int and self._account>0,"account_unconfirmed")

    def _request(self, method, path, body=None, form=False):
        payload = None if body is None else (urlencode(body) if form else json.dumps(body)).encode()
        headers = {"Content-Type":"application/x-www-form-urlencoded" if form else "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer "+self._token
        try:
            request = Request("https://api.avito.ru"+path, data=payload, method=method, headers=headers)
            with self._opener.open(request,timeout=20) as response:
                _require(response.status==200,"unexpected_status")
                content=response.read(1_000_001)
                _require(len(content)<=1_000_000,"unexpected_response_size")
                result=json.loads(content)
                _require(isinstance(result,dict),"response_schema")
                return result
        except HTTPError as exc:
            exc.close()
            raise StockError("http_"+str(exc.code)) from None
        except (OSError, ValueError, EOFError, HTTPException):
            raise StockError("transport_error") from None

    def link_issues(self):
        """Map unsafe bindings to SKU-specific codes; fail on a malformed global response."""
        aliases = {key:sku for sku in LISTINGS for key in (sku,sku+"-av")}
        query = urlencode({"query":",".join(aliases)})
        reply = self._request("GET","/autoload/v2/items/avito_ids?"+query)
        links,owners,issues = {},{},{}
        for row in _records(reply,"items"):
            key = row.get("ad_id")
            _require(isinstance(key,str) and key in aliases,"unexpected_autoload_response")
            sku = aliases[key]
            ident = row.get("avito_id")
            if key in links:
                issues[sku] = "duplicate_autoload_row"
            links[key] = ident
            if type(ident) is int and ident>0:
                owners.setdefault(ident,set()).add(sku)
            elif ident is not None:
                issues[sku] = "invalid_autoload_identifier"
        for sku,expected in LISTINGS.items():
            if type(links.get(sku)) is not int or links[sku]!=expected or links.get(sku+"-av") is not None:
                issues[sku] = "historical_link_changed"
            if len(owners.get(expected,()))>1:
                issues[sku] = "listing_linked_to_another_sku"
        return issues

    def verify_listing(self, sku):
        ident = _listing(sku)
        problem = self.link_issues().get(sku)
        if problem:
            raise StockError(problem)
        detail = self._request("GET",f"/core/v1/accounts/{self._account}/items/{ident}/")
        _require(detail.get("status")=="active","listing_not_active")
        reverse = detail.get("autoload_item_id")
        _require(reverse is None or reverse=="" or reverse==sku,"reverse_link_conflicts")

    def read(self, sku):
        """Strong available quantity after Avito reservations; None means unlimited."""
        ident = _listing(sku)
        reply = self._request("POST","/stock-management/1/info",{"item_ids":[ident],"strong_consistency":True})
        rows = _records(reply,"stocks")
        _require(len(rows)==1 and type(rows[0].get("item_id")) is int and rows[0]["item_id"]==ident,
                 "stock_identity_unconfirmed")
        row = rows[0]
        _require(all(type(row.get(k)) is bool for k in ("is_multiple","is_unlimited","is_out_of_stock"))
                 and type(row.get("quantity")) is int,"stock_flags_unconfirmed")
        if row["is_unlimited"]:
            return None
        qty = row["quantity"]
        _require(qty>=0 and row["is_out_of_stock"]==(qty==0),"inconsistent_stock")
        return qty

    def set_quantity(self, sku, quantity):
        """One absolute PUT per listing per process. Caller independently reads resulting free stock."""
        ident = _listing(sku)
        _require(type(quantity) is int and 0<=quantity<=999999,"invalid_quantity")
        self.verify_listing(sku)
        with _SEND_LOCK:
            _require(ident not in _SENT_IDS,"duplicate_write_prevented")
            _SENT_IDS.add(ident)
        reply = self._request("PUT","/stock-management/1/stocks",
                              {"stocks":[{"item_id":ident,"quantity":quantity}]})
        rows = _records(reply,"stocks")
        _require(len(rows)==1 and type(rows[0].get("item_id")) is int and rows[0]["item_id"]==ident,
                 "receipt_identity_unconfirmed")
        row = rows[0]
        _require(row.get("success") is True and ("errors" not in row or row["errors"]==[]),"item_rejected")
        return {"item_id":ident,"success":True}
