"""Recover exact approved 6T30 Library originals from a GitHub connector bridge.

Two files are native Git blobs; the third is four base64 parts because the
connector has an individual payload limit. No image reconstruction/AI substitution.
The output is compatible with the existing verified ZIP publication pipeline.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "data/leoparts_6t30_approved_images_sha256_20261123.json"
INTAKE = ROOT / "data/product_intake_v2/requests/leoparts-five-20261123.json"
MEDIA = ROOT / "assets/product-intake-v2/leoparts-five-20261123"
BRIDGE = ROOT / "assets/product-intake-v2/bridge"
OUTPUT = ROOT / "LEOPARTS_3_approved_6T30_images_exact.zip"
SKUS = ("6t30-solenoids-gen2", "6t30-tcm-gen1", "6t30-tcm-gen2")
SOURCE = {
    "6t30-solenoids-gen2": MEDIA / "6t30-solenoids-gen2_1.png",
    "6t30-tcm-gen1": MEDIA / "6t30-tcm-gen1_1.png",
}
PARTS = tuple(BRIDGE / f"6t30-tcm-gen2.part{i:02}.b64.txt" for i in range(1, 5))

def restore() -> dict:
    proof = json.loads(PROOF.read_text(encoding="utf-8"))
    intake = json.loads(INTAKE.read_text(encoding="utf-8"))
    assert proof.get("approved") is True and intake.get("approved") is True
    approved = {r["sku"]: r for r in proof["assets"]}
    products = {r["sku"]: r for r in intake["items"]}
    if set(approved) != set(SKUS) or not set(SKUS).issubset(products):
        raise ValueError("Unexpected/missing approved 6T30 SKU")
    if len(PARTS) != 4 or any(not p.is_file() for p in PARTS):
        raise FileNotFoundError("Incomplete exact Gen2 TCM four-part media bridge")
    encoded = b"".join(p.read_bytes() for p in PARTS)
    if len(encoded) != 4511440:
        raise ValueError("Gen2 TCM base64 length differs from approved original")
    recovered = base64.b64decode(encoded, validate=True)
    blobs = {sku: path.read_bytes() for sku, path in SOURCE.items()}
    blobs["6t30-tcm-gen2"] = recovered
    mapping = []
    for sku in SKUS:
        raw = blobs[sku]
        actual = hashlib.sha256(raw).hexdigest()
        if actual != approved[sku]["sha256"] or actual != products[sku]["media"][0]["sha256"]:
            raise ValueError("Approved exact PNG hash mismatch for " + sku)
        with Image.open(io.BytesIO(raw)) as png:
            if png.format != "PNG" or list(png.size) != approved[sku]["dimensions"]:
                raise ValueError("Approved PNG dimension/format mismatch for " + sku)
        prices = products[sku]["prices"]
        if (prices["ozon"] != prices["wildberries"] or
                prices["wildberries"] != prices["yandex_market"] or
                prices["avito"] != prices["tilda"]):
            raise ValueError("Approved price relationship changed for " + sku)
        mapping.append({
            "sku": sku, "source_filename": approved[sku]["filename"],
            "zip_path": f"photos/{sku}/{sku}_1.png",
            "sha256": actual,
            "marketplace_price_rub": prices["ozon"],
            "avito_site_price_rub": prices["avito"],
            "stock": products[sku]["stock"],
        })
    note = {"notes": "Exact original user-approved 6T30 PNGs, verified SHA256 2026-11-23; never substitute generations.",
            "products": mapping}
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_STORED) as archive:
        for sku in SKUS:
            archive.writestr(f"photos/{sku}/{sku}_1.png", blobs[sku])
        archive.writestr("PHOTO_MAPPING_AND_PRICES.json",
                         json.dumps(note, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(OUTPUT) as archive:
        if len(archive.namelist()) != 4:
            raise ValueError("Archive must contain exactly 3 originals plus mapping")
        for row in mapping:
            if hashlib.sha256(archive.read(row["zip_path"])).hexdigest() != row["sha256"]:
                raise ValueError("Archive integrity failed")
    return {"status": "verified_exact_library_origins", "skus": SKUS,
            "sha256": {r["sku"]: r["sha256"] for r in mapping},
            "output": OUTPUT.name, "bytes": OUTPUT.stat().st_size}

if __name__ == "__main__":
    print(json.dumps(restore(), ensure_ascii=False), flush=True)
