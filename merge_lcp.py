#!/usr/bin/env python3
"""LCP Merger — cherry-pick licenses and standalone content from source LCPs into a target LCP."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile

# Categories where items belong to licenses (have license/license_level fields)
LICENSED_CATEGORIES = ["weapons.json", "systems.json", "mods.json"]

# Categories with standalone (non-licensed) content
STANDALONE_CATEGORIES = ["talents.json", "core_bonuses.json", "pilot_gear.json", "actions.json"]

EXCLUDED = {
    "npc_classes.json",
    "npc_templates.json",
    "npc_features.json",
    "manufacturers.json",
    "lcp_manifest.json",
}


def extract_lcp(lcp_path, dest):
    with zipfile.ZipFile(lcp_path, "r") as zf:
        zf.extractall(dest)


def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def item_name(item):
    return item.get("name") or item.get("id") or "???"


def prompt_select(options, header):
    """Generic selection prompt. Returns list of selected indices (0-based)."""
    print(f"\n{header}")
    for i, label in enumerate(options, 1):
        print(f"  {i}. {label}")
    print(f"  a. All")
    print(f"  s. Skip")

    while True:
        choice = input("Select (comma-separated numbers, 'a' for all, 's' to skip): ").strip().lower()
        if choice == "s":
            return []
        if choice == "a":
            return list(range(len(options)))
        try:
            indices = [int(x.strip()) - 1 for x in choice.split(",")]
            if all(0 <= i < len(options) for i in indices):
                return indices
        except ValueError:
            pass
        print("Invalid selection, try again.")


def build_licenses(source_dir):
    """Build a map of license_name -> {frame, items} from source LCP."""
    frames = load_json(os.path.join(source_dir, "frames.json"))
    licenses = {}

    # Register frames as license anchors — match by name (what weapons/systems reference)
    for fr in frames:
        key = fr.get("name", "").upper()
        licenses.setdefault(key, {"frame": None, "items": []})
        licenses[key]["frame"] = fr

    # Collect licensed items from weapons, systems, mods
    unlicensed = []  # items with no license field
    for category in LICENSED_CATEGORIES:
        path = os.path.join(source_dir, category)
        if not os.path.exists(path):
            continue
        for item in load_json(path):
            lic = item.get("license")
            if lic:
                key = lic.upper()
                licenses.setdefault(key, {"frame": None, "items": []})
                licenses[key]["items"].append((category, item))
            else:
                # Unlicensed items (integrated weapons, generic gear)
                unlicensed.append((category, item))

    return licenses, unlicensed


def format_license_label(key, data):
    """Format a license for display in the selection menu."""
    frame = data["frame"]
    items = data["items"]
    parts = []
    if frame:
        size = frame.get("stats", {}).get("size", "?")
        mtypes = ", ".join(frame.get("mechtype", []))
        parts.append(f"Frame: {item_name(frame)} (size {size}, {mtypes})")
    else:
        parts.append("(no frame in this LCP)")

    # Summarize items by LL
    by_ll = {}
    for cat, item in items:
        ll = item.get("license_level", "?")
        by_ll.setdefault(ll, []).append(item_name(item))
    for ll in sorted(by_ll.keys(), key=lambda x: (isinstance(x, str), x)):
        names = ", ".join(by_ll[ll])
        parts.append(f"LL{ll}: {names}")

    return f"{key} — " + " | ".join(parts)


def rewrite_source(item, target_source="ferum_vox"):
    if "source" in item and item["source"]:
        item["source"] = target_source


def bump_version(version_str):
    parts = version_str.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def merge_into_target(target_dir, category, items):
    """Append items to the target category JSON file."""
    path = os.path.join(target_dir, category)
    existing = load_json(path)
    existing.extend(items)
    save_json(path, existing)


def main():
    parser = argparse.ArgumentParser(description="Merge licenses and content from a source LCP into a target LCP")
    parser.add_argument("target", help="Target LCP file (e.g. ferum-vox.lcp)")
    parser.add_argument("source", help="Source LCP file to cherry-pick from")
    parser.add_argument("--bump", action="store_true", help="Bump the target LCP version")
    args = parser.parse_args()

    if not os.path.exists(args.target):
        sys.exit(f"Target LCP not found: {args.target}")
    if not os.path.exists(args.source):
        sys.exit(f"Source LCP not found: {args.source}")

    tmp = tempfile.mkdtemp(prefix="lcp_merge_")
    target_dir = os.path.join(tmp, "target")
    source_dir = os.path.join(tmp, "source")
    os.makedirs(target_dir)
    os.makedirs(source_dir)

    try:
        extract_lcp(args.target, target_dir)
        extract_lcp(args.source, source_dir)

        source_manifest = load_json(os.path.join(source_dir, "lcp_manifest.json"))
        source_name = source_manifest.get("name", "Source LCP") if isinstance(source_manifest, dict) else "Source LCP"

        print(f"Merging from: {source_name}")
        print(f"Into: {args.target}")

        anything_merged = False

        # === LICENSES ===
        licenses, unlicensed = build_licenses(source_dir)

        if licenses:
            labels = []
            keys = sorted(licenses.keys())
            for key in keys:
                labels.append(format_license_label(key, licenses[key]))

            selected_idx = prompt_select(labels, f"Licenses in {source_name}:")

            for idx in selected_idx:
                key = keys[idx]
                data = licenses[key]

                # Merge frame
                if data["frame"]:
                    fr = data["frame"]
                    rewrite_source(fr)
                    merge_into_target(target_dir, "frames.json", [fr])
                    print(f"  + Frame: {item_name(fr)}")

                # Merge licensed items grouped by category
                by_cat = {}
                for cat, item in data["items"]:
                    by_cat.setdefault(cat, []).append(item)
                for cat, items in by_cat.items():
                    for item in items:
                        rewrite_source(item)
                    merge_into_target(target_dir, cat, items)
                    names = ", ".join(item_name(i) for i in items)
                    label = cat.replace(".json", "")
                    print(f"  + {label}: {names}")

                anything_merged = True

        # === UNLICENSED ITEMS (integrated weapons, generic gear) ===
        if unlicensed:
            labels = [f"{item_name(item)} ({cat.replace('.json','')})" for cat, item in unlicensed]
            selected_idx = prompt_select(labels, f"Unlicensed items in {source_name}:")

            for idx in selected_idx:
                cat, item = unlicensed[idx]
                rewrite_source(item)
                merge_into_target(target_dir, cat, [item])
                print(f"  + {item_name(item)}")
                anything_merged = True

        # === STANDALONE CATEGORIES (talents, core bonuses, pilot gear, actions) ===
        for category in STANDALONE_CATEGORIES:
            path = os.path.join(source_dir, category)
            if not os.path.exists(path):
                continue
            items = load_json(path)
            if not items:
                continue

            label = category.replace(".json", "").replace("_", " ")
            labels = [item_name(item) for item in items]
            selected_idx = prompt_select(labels, f"{label.title()} in {source_name}:")

            for idx in selected_idx:
                item = items[idx]
                rewrite_source(item)
                merge_into_target(target_dir, category, [item])
                print(f"  + {item_name(item)}")
                anything_merged = True

        if not anything_merged:
            print("\nNothing selected. No changes made.")
            return

        # Version bump
        manifest_path = os.path.join(target_dir, "lcp_manifest.json")
        manifest = load_json(manifest_path)
        if isinstance(manifest, dict):
            if args.bump:
                old_ver = manifest.get("version", "0")
                new_ver = bump_version(old_ver)
                manifest["version"] = new_ver
                print(f"\nVersion: {old_ver} -> {new_ver}")
            save_json(manifest_path, manifest)

        # Re-zip
        with zipfile.ZipFile(args.target, "w", zipfile.ZIP_STORED) as zf:
            for fname in sorted(os.listdir(target_dir)):
                fpath = os.path.join(target_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, fname)

        print(f"\nDone, updated {args.target}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
