"""Temporarily withhold the single matched small gasket, preserving its listing ID."""
import base64
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import time
from urllib.parse import urlencode

from openpyxl import load_workbook

sys.path.insert(0, str(Path.cwd()))
import avito_client
from scripts.apply_dq200_prices_20261120 import gh, get_avito_asset, rename_asset, STABLE_AVITO_URL
from scripts.publish_avito_titles_20261121 import verified_download

SKU = "0cw0am"
ITEM = 8386987927
SHEET = "Объявления"
PATH = "data/avito_feed.xlsx"
OLD_TITLE = "Прокладка электронной платы мехатроника DSG7 DQ200"


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def binding():
    reply = avito_client._request("GET", "/autoload/v2/items/avito_ids",
                                  params={"query": ",".join((SKU, SKU + "-av", "0cw0amdsgdq200"))})
    rows = reply["items"]
    exact = [r.get("avito_id") for r in rows if r.get("ad_id") == SKU]
    require(exact == [ITEM], "Target binding differs")
    require(not any(r.get("ad_id") != SKU and r.get("avito_id") == ITEM for r in rows), "Item claimed by another offer")
    require(not any(r.get("ad_id") == SKU + "-av" and r.get("avito_id") for r in rows), "Second binding requires matching")


def stock():
    reply = avito_client._request("POST", "/stock-management/1/info", json={"item_ids": [ITEM], "strong_consistency": True})
    rows = reply.get("stocks", [])
    require(len(rows) == 1 and rows[0].get("item_id") == ITEM, "Quantity receipt belongs to a different item")
    row = rows[0]
    require(type(row.get("is_unlimited")) is bool and type(row.get("is_out_of_stock")) is bool, "Missing stock flags")
    return row["quantity"], row["is_unlimited"], row["is_out_of_stock"]


def withhold():
    binding()
    account = avito_client._request("GET", "/core/v1/accounts/self")["id"]
    status_path = f"/core/v1/accounts/{account}/items/{ITEM}/"
    item = avito_client._request("GET", status_path)
    require(item.get("autoload_item_id") in (None, "", SKU), "Reverse ID changed")
    require(item.get("id") in (None, ITEM), "Account item differs")
    require(item.get("title") in (None,OLD_TITLE), "Item title changed")
    print("AVITO_HOLD_LISTING_BEFORE", ITEM, item.get("status"), flush=True)
    if item.get("status") == "active":
        state = stock()
        print("AVITO_HOLD_STOCK_BEFORE", *state, flush=True)
        if state != (0,False,True):
            receipt = avito_client._request("PUT", "/stock-management/1/stocks", retries=1,
                                            json={"stocks": [{"item_id": ITEM, "quantity": 0}]})
            result = receipt.get("stocks", [])
            require(len(result)==1 and result[0].get("item_id")==ITEM and result[0].get("success") is True and not result[0].get("errors"), "Quantity update not accepted")
        for delay in (0,2,8,20):
            time.sleep(delay)
            state=stock()
            if state == (0,False,True):
                break
        require(state==(0,False,True), "Zero not yet externally verified")
        print("AVITO_HOLD_STOCK_VERIFIED", ITEM, "0, out_of_stock", flush=True)
    print("AVITO_HOLD_LISTING_AFTER", ITEM, avito_client._request("GET",status_path).get("status"), flush=True)


def without_target(raw):
    book = load_workbook(BytesIO(raw))
    ws=book[SHEET]
    matches=[r[0].row for r in ws if r[3].value==SKU]
    if not matches:
        return raw
    require(len(matches)==1, "Duplicate SKU rows")
    row=matches[0]
    require(ws.cell(row,24).value==OLD_TITLE, "Feed title differs for the selected gasket")
    before={s.title:list(s.values) for s in book}
    ws.delete_rows(row)
    del before[SHEET][row-1]
    out=BytesIO()
    book.save(out)
    result=out.getvalue()
    actual=load_workbook(BytesIO(result), read_only=True)
    require(all(list(actual[s].values)==v for s,v in before.items()) and list(before)==actual.sheetnames, "Other feed rows differ")
    return result


def source():
    url="/contents/"+PATH
    record=gh("GET",url,params={"ref":"main"}).json()
    raw=base64.b64decode(record["content"])
    stripped=without_target(raw)
    if raw!=stripped:
        gh("PUT",url,json={"message":"Withhold only the matched small gasket from the Avito feed [skip ci]","branch":"main","sha":record["sha"],"content":base64.b64encode(stripped).decode()})
    latest=base64.b64decode(gh("GET",url,params={"ref":"main"}).json()["content"])
    require(without_target(latest)==latest, "Source readback failed")
    print("AVITO_HOLD_SOURCE_VERIFIED", SKU, flush=True)


def release():
    release,asset,raw=get_avito_asset()
    stripped=without_target(raw)
    if stripped!=raw:
        suffix=sha256(stripped).hexdigest()[:14]
        stage_name="avito_feed.hold_"+suffix+".xlsx"
        stage=next((a for a in release["assets"] if a["name"]==stage_name),None)
        if stage is None:
            stage=gh("POST",release["upload_url"].split("{",1)[0],params={"name":stage_name},data=stripped,
                     headers={"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}).json()
        verified_download(stage["browser_download_url"],stripped)
        _,last,data=get_avito_asset()
        require(last["id"]==asset["id"] and data==raw,"Stable feed changed before publication")
        backup="avito_feed.before_small_gasket_hold_"+str(asset["id"])+".xlsx"
        rename_asset(asset["id"],backup)
        try:
            rename_asset(stage["id"],"avito_feed.xlsx")
        except Exception:
            rename_asset(asset["id"],"avito_feed.xlsx")
            raise
    verified_download(STABLE_AVITO_URL,stripped)
    print("AVITO_HOLD_STABLE_FEED_VERIFIED",SKU,sha256(stripped).hexdigest(),flush=True)


if __name__=="__main__":
    mode=sys.argv[1]
    try:
        if mode=="stock":
            withhold()
        elif mode=="feed":
            source()
            release()
        else:
            raise ValueError("Unknown mode")
    except Exception as exc:
        print("AVITO_HOLD_ERROR",mode,type(exc).__name__,str(exc)[:140] if type(exc) is ValueError else "",flush=True)
        sys.exit(1)
