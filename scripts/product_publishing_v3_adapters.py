"""LEOPARTS v3 writer adapters. Every write is scoped to a verified NEW SKU.

Adapters never decide whether to write. The orchestrator MUST checkpoint a
durable intent before calling submit/price/stock/media, and check live seller
existence first. No API calls to Avito, no updates of existing seller cards.
"""
from __future__ import annotations

import os
from typing import Any


class AdapterGuard(ValueError):
    pass


def require(ok: bool, why: str) -> None:
    if not ok:
        raise AdapterGuard(why)


def attribute(attribute_id: int, value: str | int, dictionary_id: int = 0) -> dict:
    return {"id": attribute_id, "complex_id": 0,
            "values": [{"value": str(value), "dictionary_value_id": int(dictionary_id)}]}


def exact(rows: list[dict], sku: str, field: str) -> list[dict]:
    return [x for x in rows if str(x.get(field) or "").casefold() == sku.casefold()]


class OzonAdapter:
    name = "ozon"

    def __init__(self):
        import ozon_client as oz
        self.api = oz
        self.cards: list[dict] = []
        self.required: dict[tuple[int, int], list[dict]] = {}
        self.customs: dict[tuple[int, int, str], dict] = {}

    def scan(self) -> None:
        self.cards = self.api.list_products(limit=100)

    def existing(self, sku: str) -> list[dict]:
        return exact(self.cards, sku, "offer_id")

    def _mandatory(self, category: int, type_id: int) -> list[dict]:
        key = (category, type_id)
        if key not in self.required:
            got = self.api.get_category_attributes(category, type_id)
            require(isinstance(got, list) and len(got) > 0,
                    "Ozon category attributes unavailable: fail closed")
            self.required[key] = got
        return self.required[key]

    def prepare(self, item: dict, urls: list[str], paths: list, *, stock: int) -> dict:
        sku = item["sku"]
        cfg = (item.get("categories") or {}).get("ozon") or {}
        require(cfg.get("verified") is True, "Ozon category must be explicitly verified")
        cat, typ = cfg.get("description_category_id"), cfg.get("type_id")
        require(type(cat) is int and cat > 0 and type(typ) is int and typ > 0,
                "Ozon exact category/type IDs missing")
        code, evidence = cfg.get("tnved_code"), cfg.get("customs_evidence")
        require(isinstance(code, str) and code.isdecimal() and len(code) == 10 and evidence,
                "Ozon exact SKU TN VED code/evidence missing")
        code_key = (cat, typ, code)
        if code_key not in self.customs:
            vals = self.api.search_attribute_values(cat, typ, 22232, code, limit=25)
            hits = [x for x in vals if str(x.get("value", "")).split(" - ")[0].strip() == code]
            require(len(hits) == 1, "Ozon TN VED code not uniquely in exact category dictionary")
            self.customs[code_key] = hits[0]
        tn = self.customs[code_key]
        brand = item.get("brand") or "Нет бренда"
        brand_id = cfg.get("brand_dictionary_id")
        if brand == "Нет бренда" and brand_id is None:
            brand_id = 126745801  # real Ozon 'Нет бренда' dictionary ID verified in LEOPARTS
        require(type(brand_id) is int and brand_id > 0,
                "Ozon exact brand dictionary ID not verified")
        raw_attrs = cfg.get("attributes") or []
        require(isinstance(raw_attrs, list) and all(type(a) is dict for a in raw_attrs),
                "Ozon additional attributes must be approved dictionary objects")
        locked = {85, 22232, 23536, 4180, 4191, 7236, 9024}
        require(not any(a.get("id") in locked for a in raw_attrs),
                "Cannot override protected brand/customs/identity attributes")
        provided = locked | {x.get("id") for x in raw_attrs} | {8229}
        required = {int(a["id"]) for a in self._mandatory(cat, typ)
                    if a.get("is_required") and a.get("id")}
        require(required.issubset(provided),
                "Ozon mandatory attributes missing: " + str(sorted(required - provided)))
        assert urls and all(x.startswith("https://") for x in urls)
        d = item["dimensions"]
        for k in ("length_mm", "width_mm", "height_mm", "weight_g"):
            require(type(d[k]) in (int, float) and d[k] > 0, "Invalid package dimension: " + k)
        name = item.get("ozon_title") or item["title"]
        require(isinstance(name, str) and name.strip(), "Missing Ozon-specific approved title")
        attrs = [
            attribute(85, brand, brand_id),
            attribute(22232, tn["value"], int(tn["id"])),
            attribute(23536, "false"),
            attribute(4180, name),
            attribute(4191, item["description"]),
            attribute(7236, cfg.get("manufacturer_article") or sku),
            attribute(9024, sku),
        ] + raw_attrs
        return {
            "offer_id": sku, "name": name,
            "description_category_id": cat, "type_id": typ,
            "attributes": attrs, "complex_attributes": [],
            "images": urls[1:], "primary_image": urls[0], "color_image": "",
            "currency_code": "RUB", "price": str(item["prices"]["ozon"]),
            "old_price": "0", "vat": str(cfg.get("vat") or "0"),
            "depth": round(d["length_mm"]), "width": round(d["width_mm"]),
            "height": round(d["height_mm"]), "dimension_unit": "mm",
            "weight": round(d["weight_g"]), "weight_unit": "g",
            "barcode": "", "pdf_list": []
        }

    def submit(self, item: dict, payload: dict) -> dict:
        require(payload["offer_id"] == item["sku"], "Ozon submit SKU mismatch")
        require(payload["price"] == str(item["prices"]["ozon"]), "Ozon price frozen")
        reply = self.api.import_products([payload])
        task = (reply.get("result") or {}).get("task_id")
        require(type(task) is int and task > 0, "Ozon import has no task ID: outcome unknown")
        return {"task_id": task}

    def observe(self, item: dict, submission: dict) -> dict:
        sku = item["sku"]
        rows = self.api.get_product_info_list([sku])
        require(not any(x.get("_error") for x in rows),
                "Ozon detail readback unavailable")
        found = exact(rows, sku, "offer_id")
        if not found:
            result = {"state": "not_created"}
            if submission.get("task_id"):
                details = self.api.get_import_status(int(submission["task_id"]))
                results = (details.get("result") or {}).get("items") or []
                target = exact(results, sku, "offer_id")
                if target:
                    result["import_status"] = target[0].get("status")
                    result["import_errors"] = target[0].get("errors")
                    if target[0].get("status") == "failed" or target[0].get("errors"):
                        result["state"] = "rejected"
            return result
        require(len(found) == 1, "Ozon duplicate card")
        card = found[0]
        st = card.get("statuses") or {}
        state = "created" if st.get("is_created") else "moderating"
        if card.get("errors") or st.get("moderate_status") == "declined":
            state = "rejected"
        elif st.get("status_name") == "Продаётся" and st.get("is_created"):
            state = "selling"
        return {"state": state, "product_id": card.get("id") or card.get("product_id"),
                "moderation": st.get("moderate_status"), "status_name": st.get("status_name"),
                "errors": card.get("errors") or []}

    def set_stock(self, item: dict, submission: dict, amount: int) -> dict:
        warehouse = os.getenv("OZON_WAREHOUSE_ID", "").strip()
        require(warehouse.isdecimal() and int(warehouse) > 0,
                "Missing verified OZON_WAREHOUSE_ID")
        reply = self.api.update_stocks([{"offer_id": item["sku"],
            "stock": amount, "warehouse_id": int(warehouse)}])
        rows = reply.get("result") or []
        matches = exact(rows, item["sku"], "offer_id")
        require(len(matches) == 1 and matches[0].get("updated") is True
                and not matches[0].get("errors"), "Ozon stock API not acknowledged")
        return {"warehouse_id": int(warehouse), "stock": amount}

    def set_price(self, item: dict, submission: dict) -> dict:
        # The only Ozon price write is part of create payload.
        return {"mode": "price_from_approved_import", "price": item["prices"]["ozon"]}

    def set_media(self, item: dict, submission: dict, urls: list[str]) -> dict:
        # Photo URLs are already in original create payload.
        return {"mode": "media_from_approved_import", "images": len(urls)}


class WBAdapter:
    name = "wildberries"

    def __init__(self):
        import wb_client as wb
        self.api = wb
        self.cards: list[dict] = []

    def scan(self) -> None:
        self.cards = self.api.list_cards(limit=100)

    def existing(self, sku: str) -> list[dict]:
        return exact(self.cards, sku, "vendorCode")

    def prepare(self, item: dict, urls: list[str], paths: list, *, stock: int) -> dict:
        cfg = (item.get("categories") or {}).get("wildberries") or {}
        require(cfg.get("verified") is True and type(cfg.get("subject_id")) is int
                and cfg["subject_id"] > 0 and type(cfg.get("characteristics")) is list,
                "WB exact subject/characteristics must be approved")
        title = item.get("wb_title") or item["title"]
        require(type(title) is str and len(title) <= 60, "WB title >60 or missing")
        dims = item["dimensions"]
        require(urls, "WB approved images missing")
        return {"subject_id": cfg["subject_id"],
                "title": title, "brand": item.get("brand") or "Нет бренда",
                "description": item["description"],
                "dimensions": {"length": max(1, round(dims["length_mm"]/10)),
                               "width": max(1, round(dims["width_mm"]/10)),
                               "height": max(1, round(dims["height_mm"]/10)),
                               "weightBrutto": round(dims["weight_g"]/1000, 3)},
                "characteristics": cfg["characteristics"],
                "barcode": cfg.get("barcode")}

    def submit(self, item: dict, payload: dict) -> dict:
        barcode = payload.get("barcode")
        if barcode is None:
            candidates = self.api.generate_barcodes(1)
            require(len(candidates) == 1, "WB barcode generation returned no unique barcode")
            barcode = str(candidates[0])
        card = {
            "vendorCode": item["sku"], "title": payload["title"],
            "description": payload["description"], "brand": payload["brand"],
            "dimensions": payload["dimensions"], "characteristics": payload["characteristics"],
            "sizes": [{"techSize": "0", "wbSize": "", "price": item["prices"]["wildberries"],
                       "skus": [barcode]}]
        }
        result = self.api.create_cards(int(payload["subject_id"]), [card])
        require(not result.get("error"), "WB asynchronous card upload rejected")
        return {"barcode": barcode, "submitted": True}

    def observe(self, item: dict, submission: dict) -> dict:
        # WB card creation is async, refresh only when resolving this SKU.
        cards = self.api.list_cards(limit=100)
        matches = exact(cards, item["sku"], "vendorCode")
        require(len(matches) <= 1, "WB duplicate SKU")
        if not matches:
            issues = self.api.get_upload_errors(limit=100) if submission.get("submitted") else []
            related = [x for x in issues if item["sku"].casefold()
                       in str(x.get("vendorCodes") or x.get("vendorCode") or x).casefold()]
            return {"state": "rejected" if related else "moderating",
                    "errors": related[:3]}
        x = matches[0]
        return {"state": "created", "nm_id": x.get("nmID"),
                "photo_count": len(x.get("photos") or []),
                "barcode": (x.get("sizes") or [{}])[0].get("skus", [None])[0],
                "errors": []}

    def set_media(self, item: dict, submission: dict, urls: list[str]) -> dict:
        cards = self.api.list_cards(limit=100)
        matches = exact(cards, item["sku"], "vendorCode")
        require(len(matches) == 1 and matches[0].get("nmID"), "WB new card not visible")
        result = self.api.update_media(int(matches[0]["nmID"]), urls)
        require(not result.get("error"), "WB media submission rejected")
        return {"nm_id": matches[0]["nmID"], "submitted_images": len(urls)}

    def set_price(self, item: dict, submission: dict) -> dict:
        rows = exact(self.api.list_cards(limit=100), item["sku"], "vendorCode")
        require(len(rows) == 1 and rows[0].get("nmID"), "WB new card absent for price")
        nm = int(rows[0]["nmID"])
        reply = self.api.update_prices([{"nmID": nm, "price": item["prices"]["wildberries"], "discount": 0}])
        require(not reply.get("error"), "WB price upload rejected")
        task = (reply.get("data") or {}).get("id") or (reply.get("data") or {}).get("uploadID")
        return {"nm_id": nm, "price": item["prices"]["wildberries"],
                "price_task_id": task, "status": "submitted"}

    def set_stock(self, item: dict, submission: dict, amount: int) -> dict:
        warehouse = os.getenv("WB_WAREHOUSE_ID", "").strip()
        require(warehouse.isdecimal() and int(warehouse) > 0,
                "Missing verified WB_WAREHOUSE_ID")
        cards = self.api.list_cards(limit=100)
        mapping = self.api.get_offer_barcode_map(cards)
        barcode = mapping.get(item["sku"])
        require(bool(barcode), "WB exact SKU barcode missing")
        result = self.api.update_stocks(warehouse, [{"sku": barcode, "amount": amount}])
        require(not result.get("error"), "WB new stock submission rejected")
        return {"stock": amount, "warehouse_id": warehouse, "barcode": barcode}


class MarketAdapter:
    name = "yandex_market"

    def __init__(self):
        import yandex_market_client as ym
        self.api = ym
        self.offers: list[dict] = []
        self.business: int | None = None

    def scan(self) -> None:
        self.business = self.api.get_business_id()
        self.offers = self.api.get_all_offers(archived=False)

    def existing(self, sku: str) -> list[dict]:
        return exact(self.offers, sku, "sku")

    def prepare(self, item: dict, urls: list[str], paths: list, *, stock: int) -> dict:
        cfg = (item.get("categories") or {}).get("yandex_market") or {}
        require(cfg.get("verified") is True and type(cfg.get("category_id")) is int
                and cfg["category_id"] > 0, "Market exact verified category missing")
        d = item["dimensions"]
        require(urls, "Market image missing")
        return {"offer": {"offerId": item["sku"], "name": item.get("market_title") or item["title"],
                          "description": item["description"], "pictures": urls,
                          "vendor": item.get("brand") or "Нет бренда",
                          "vendorCode": item["sku"], "marketCategoryId": int(cfg["category_id"]),
                          "weightDimensions": {
                              "length": d["length_mm"]/10, "width": d["width_mm"]/10,
                              "height": d["height_mm"]/10, "weight": d["weight_g"]/1000}}}

    def submit(self, item: dict, payload: dict) -> dict:
        require(self.business is not None, "Market business not verified")
        code, res = self.api._request("POST", f"/v2/businesses/{self.business}/offer-mappings/update",
                                      json_body={"offerMappings":[payload]})
        require(code == 200 and res.get("status") == "OK" and not res.get("errors"),
                "Market offer mapping not accepted; outcome unknown")
        return {"submitted": True}

    def observe(self, item: dict, submission: dict) -> dict:
        rows = self.api.get_all_offers(archived=False)
        matches = exact(rows, item["sku"], "sku")
        require(len(matches) <= 1, "Market duplicate SKU")
        if not matches:
            return {"state": "moderating", "errors": []}
        return {"state": "created", "market_sku": matches[0].get("market_sku"),
                "catalog_name": matches[0].get("name"), "errors": []}

    def set_price(self, item: dict, submission: dict) -> dict:
        ok, errors = self.api.update_prices(
            {item["sku"]: (int(item["prices"]["yandex_market"]), None)})
        require(ok == 1 and not errors, "Market new price rejected " + str(errors)[:130])
        return {"price": item["prices"]["yandex_market"], "submitted": True}

    def set_media(self, item: dict, submission: dict, urls: list[str]) -> dict:
        return {"mode": "media_from_initial_mapping", "images": len(urls)}

    def set_stock(self, item: dict, submission: dict, amount: int) -> dict:
        ok, errors = self.api.update_stocks({item["sku"]: amount})
        require(ok == 1 and not errors, "Market new stock rejected " + str(errors)[:130])
        return {"stock": amount, "submitted": True}


class TildaAdapter:
    name = "tilda"

    def __init__(self):
        import tilda_commerceml as tc
        from scripts.leoparts_direct_operation_nov21 import site_records
        self.tc = tc
        self.site_records = site_records
        self.cards: list[dict] = []

    def scan(self) -> None:
        self.cards = list(self.site_records().values())

    def existing(self, sku: str) -> list[dict]:
        return exact(self.cards, sku, "sku")

    def prepare(self, item: dict, urls: list[str], paths: list, *, stock: int) -> dict:
        require(paths and len(paths) == len(item["media"]), "Tilda needs original image bytes")
        require(all(str(p).lower().endswith((".png", ".jpg", ".jpeg", ".webp")) for p in paths),
                "Tilda unsupported file type")
        creds = ("TILDA_COMMERCEML_URL", "TILDA_COMMERCEML_USER", "TILDA_COMMERCEML_PASSWORD")
        require(all(os.getenv(k) for k in creds), "Tilda CommerceML credentials missing")
        return {"id": item["sku"], "title": item["title"], "description": item["description"],
                "price": int(item["prices"]["tilda"]), "qty": stock,
                "images": [str(p) for p in paths]}

    def submit(self, item: dict, payload: dict) -> dict:
        sku = item["sku"]
        require(payload["id"] == sku and payload["price"] == item["prices"]["tilda"],
                "Tilda price or SKU mismatch")
        image_blobs, paths = [], []
        for n, p in enumerate(payload["images"], start=1):
            data, changed_ext = self.tc._image_bytes(p, 3_000_000)
            name = f"import_files/{sku}_{n}{changed_ext or __import__('pathlib').Path(p).suffix}"
            image_blobs.append((name, data))
            paths.append(name)
        entry = {k: payload[k] for k in ("id","title","description","price","qty")}
        xml = self.tc.build_import_xml([entry], {sku: paths}).replace(
            "<Каталог>", '<Каталог СодержитТолькоИзменения="true">')
        offers = self.tc.build_offers_xml([entry]).replace(
            'СодержитТолькоИзменения="false"', 'СодержитТолькоИзменения="true"')
        require('СодержитТолькоИзменения="true"' in xml and
                'СодержитТолькоИзменения="true"' in offers, "Tilda delta-import not confirmed")
        creds = [os.environ[k] for k in ("TILDA_COMMERCEML_URL",
                                          "TILDA_COMMERCEML_USER", "TILDA_COMMERCEML_PASSWORD")]
        audit = self.tc.push(*creds, xml, offers, timeout=110, image_files=image_blobs)
        return {"submitted": True, "import_events": len(audit)}

    def observe(self, item: dict, submission: dict) -> dict:
        rows = exact(list(self.site_records().values()), item["sku"], "sku")
        require(len(rows) <= 1, "Tilda duplicate SKU")
        if not rows:
            return {"state": "moderating", "errors": []}
        return {"state": "created", "uid": rows[0].get("uid"),
                "price": rows[0].get("price"), "errors": []}

    def set_price(self, item: dict, submission: dict) -> dict:
        return {"mode":"price_from_initial_import", "price":item["prices"]["tilda"]}

    def set_media(self, item: dict, submission: dict, urls: list[str]) -> dict:
        return {"mode":"media_from_initial_import", "images":len(urls)}

    def set_stock(self, item: dict, submission: dict, amount: int) -> dict:
        return {"mode":"stock_from_initial_import", "stock":amount}


def factory(channel: str):
    lookup = {"ozon": OzonAdapter, "wildberries": WBAdapter,
              "yandex_market": MarketAdapter, "tilda": TildaAdapter}
    require(channel in lookup, "Unsupported / disabled channel: " + channel)
    return lookup[channel]()
