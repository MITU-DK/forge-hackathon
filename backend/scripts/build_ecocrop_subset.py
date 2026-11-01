"""Rebuild backend/data/ecocrop_subset.csv from the FAO EcoCrop database (nothing in it is typed by hand).

    cd backend && python -m scripts.build_ecocrop_subset

FAO discontinued the EcoCrop web tool; the full database (2,568 species) is republished as EcoCrop_DB.csv by UKCEH
(https://github.com/OpenCLIM/ecocrop, code under the Open Government Licence; data from https://gaez.fao.org/pages/ecocrop).
The URL is pinned to a commit so the rows cannot change under us. Only annual field crops are kept: the scoring in
app/decision/crop_fit.py is the annual-crop method of that repository, not the perennial one.
"""
import csv
import hashlib
import io
import sys
from pathlib import Path

import httpx

COMMIT = "f13577f4237604b15d85d8e43741714a2d1eb2d4"
URL = f"https://raw.githubusercontent.com/OpenCLIM/ecocrop/{COMMIT}/EcoCrop_DB.csv"
OUT = Path(__file__).resolve().parent.parent / "data" / "ecocrop_subset.csv"

# Our crop key (as in app/tools/mandi.py ALIASES) -> the exact ScientificName of the EcoCrop row.
CROPS = {
    "onion": "Allium cepa", "tomato": "Lycopersicon esculentum", "potato": "Solanum tuberosum", "wheat": "Triticum aestivum",
    "maize": "Zea mays", "rice": "Oryza sativa", "soybean": "Glycine max", "cotton": "Gossypium hirsutum", "groundnut": "Arachis hypogaea",
    "gram": "Cicer arietinum", "mustard": "Brassica juncea", "bajra": "Pennisetum glaucum", "jowar": "Sorghum bicolor",
    "cabbage": "Brassica oleracea var. capitata", "cauliflower": "Brassica oleracea var. botrytis", "brinjal": "Solanum melongena",
    "garlic": "Allium sativum", "chilli": "Capsicum annuum", "moong": "Vigna radiata", "urad": "Vigna mungo", "tur": "Cajanus cajan",
    "coriander": "Coriandrum sativum", "okra": "Abelmoschus esculentus", "pea": "Pisum sativum", "lentil": "Lens culinaris",
    "sesame": "Sesamum indicum", "sunflower": "Helianthus annuus", "safflower": "Carthamus tinctorius", "spinach": "Spinacia oleracea", "carrot": "Daucus carota",
}
KEEP = ["TOPMN", "TOPMX", "TMIN", "TMAX", "ROPMN", "ROPMX", "RMIN", "RMAX", "PHOPMN", "PHOPMX", "PHMIN", "PHMAX", "GMIN", "GMAX", "KTMP", "TEXT", "TEXTR", "DRA"]


def main() -> int:
    raw = httpx.get(URL, timeout=60, follow_redirects=True).raise_for_status().content
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", errors="ignore"))))
    by_name = {}
    for r in rows:
        by_name.setdefault(r["ScientificName"].strip(), r)  # first row for a name: the database's own primary entry
    missing = [k for k, n in CROPS.items() if n not in by_name]
    if missing:
        print("missing rows for:", missing)
        return 1
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["crop", "ScientificName", "CommonName"] + KEEP)
        for k, n in CROPS.items():
            r = by_name[n]
            w.writerow([k, n, (r.get("COMNAME") or "").split(",")[0].strip()] + [r.get(c, "").strip() for c in KEEP])
    print(f"wrote {OUT} ({len(CROPS)} crops) from commit {COMMIT}, sha256 of source {hashlib.sha256(raw).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
