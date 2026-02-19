import json
from pathlib import Path


def test_module_manifest_required_fields() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "module.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["module_id"] == "brand_studio"
    assert manifest["backend"]["router_import"] == "venom_module_brand_studio.api.routes:router"
    assert manifest["backend"]["module_api_version"]
    assert manifest["backend"]["min_core_version"]
    assert manifest["frontend"]["nav_path"] == "/brand-studio"
    assert manifest["frontend"]["feature_flag"] == "NEXT_PUBLIC_FEATURE_BRAND_STUDIO"
    assert manifest["frontend"]["nav_label"] == "Brand Studio"
    assert manifest["frontend"]["nav_labels"]["pl"] == "Brand Studio"
    assert manifest["frontend"]["nav_labels"]["en"] == "Brand Studio"
    assert manifest["frontend"]["nav_labels"]["de"] == "Brand Studio"
    assert manifest["frontend"]["component_import"]
