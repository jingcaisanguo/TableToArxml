
"""生成指定 variant 的 Autosar Builder 工程和 ARXML 清单。

用法：
    gen_ab_project.py <tools_dir> <variant_dir> <build_dir> <variant>
                      --xdm <file> [--xdm <file> ...] [oem_path]

    tools_dir    "tools" 目录的根，AB workspace 和输出目录都建在其下。
    variant_dir  含 "tresos" 配置的 variant 目录。
    build_dir    tresos 生成目录基准（build/<variant>.<build_type>）。
    variant      variant 名称，用作工程名和输出文件名。
    --xdm        要导入的导入器配置文件，每个文件重复一次该参数。
    oem_path     可选。OEM ARXML 目录的路径片段：匹配的条目改写为相对它的路径；
                 也可以用 --oem-path 指定。
"""

import argparse
import copy
import glob
import logging
import os
import posixpath
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from urllib.parse import unquote

IMPORTER = "PrefSystemImporter"

# 与 table-to-system-arxml 的默认排除规则一致；本脚本独立运行，不依赖该工具。
DEFAULT_EXCLUDE_PATTERNS = [r".*[\\/](dem|dcm)[\\/]swcd_calibration[\\/].*"]

GLOB_CHARS = "*?[]"


def has_glob(path):
    return any(char in path for char in GLOB_CHARS)


def recursive_glob(pattern):
    """通配符展开；"**.ext" 表示该目录及全部子目录中的每个 *.ext 文件。"""
    pattern = re.sub(r"(?<![^/\\])\*\*(?=[^*/\\])", lambda _: "**" + os.sep + "*", pattern)
    return glob.glob(pattern, recursive=True)


def split_glob(path):
    """把以 '/' 分隔的路径拆成第一个含通配符的段之前的部分和其余部分。"""
    parts = path.split("/")
    index = next((i for i, part in enumerate(parts) if has_glob(part)), len(parts))
    return "/".join(parts[:index]), "/".join(parts[index:])


def resolve_location_uri(uri, project_dir):
    """把 Eclipse 的 location URI（PARENT-n-PROJECT_LOC/...、file:/...）转为文件系统路径。"""
    uri = unquote(uri)
    match = re.match(r"PARENT-(\d+)-PROJECT_LOC(?:/(.*))?$", uri)
    if match:
        base = project_dir
        for _ in range(int(match.group(1))):
            base = os.path.dirname(base)
        return os.path.join(base, match.group(2) or "")
    if uri == "PROJECT_LOC" or uri.startswith("PROJECT_LOC/"):
        return os.path.join(project_dir, uri[len("PROJECT_LOC/"):])
    if uri.startswith("file:"):
        path = "/" + uri[len("file:"):].lstrip("/")
        return path[1:] if re.match(r"/[A-Za-z]:", path) else path
    return uri


def virtual_folder(name):
    link = ET.Element("link")
    ET.SubElement(link, "name").text = name
    ET.SubElement(link, "type").text = "2"
    ET.SubElement(link, "locationURI").text = "virtual:/virtual"
    return link


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tools_dir")
    parser.add_argument("variant_dir")
    parser.add_argument("build_dir")
    parser.add_argument("variant")
    parser.add_argument("oem_path", nargs="?", default=None)
    parser.add_argument("--oem-path", dest="oem_path_opt", default=None,
                        help="Same as the positional oem_path argument.")
    parser.add_argument("--xdm", action="append", dest="xdm_files", required=True,
                        help="Preference file to import (repeatable).")
    parser.add_argument("--exclude", action="append", dest="exclude_patterns",
                        help="Regex of entries to drop from the list (repeatable). "
                             "Defaults to the swcd_calibration files.")
    args = parser.parse_args(argv)

    args.oem_path = args.oem_path_opt or args.oem_path
    args.exclude_patterns = args.exclude_patterns or list(DEFAULT_EXCLUDE_PATTERNS)
    return args


def run_importer(arguments, cwd=None):
    executable = shutil.which(IMPORTER)
    if executable is None:
        logging.error("%s not found in PATH", IMPORTER)
        return False

    command = [executable] + arguments
    logging.info("Running: %s", subprocess.list2cmdline(command))
    return_code = subprocess.call(command, cwd=cwd)
    if return_code != 0:
        logging.error("%s failed with exit code %d", IMPORTER, return_code)
        return False
    return True


def gen_ab_project(args, base_path):
    workspace_dir = os.path.join(args.tools_dir, "autosar_builder", "workspace", args.variant)
    os.makedirs(workspace_dir, exist_ok=True)

    return run_importer(
        ["--ab-project", "--base-path", base_path] + args.xdm_files
        + [".project", "--env", "TRESOS_OUTPUT_BASE_DIR=%s" % args.build_dir,
           "--project", args.variant],
        cwd=workspace_dir)


def gen_file_list(args, base_path, output_file):
    return run_importer(
        ["--file-list", "--base-path", base_path] + args.xdm_files
        + [output_file, "--env", "TRESOS_OUTPUT_BASE_DIR=%s" % args.build_dir])


def expand_globs(entry, search_bases):
    """在每个查找基准目录下展开含通配符的条目。"""
    if not has_glob(entry):
        return [entry]

    matches = []
    for base in search_bases:
        pattern = entry if base is None else os.path.join(base, entry)
        matches.extend(recursive_glob(pattern))
    return matches


def expand_project_globs(project_file, root_dir):
    """把 Eclipse 工程中带通配符的 linked resource 替换为具体路径。"""
    project_dir = os.path.dirname(os.path.abspath(project_file))
    tree = ET.parse(project_file)
    linked_resources = tree.getroot().find("linkedResources")
    if linked_resources is None:
        return 0

    links = list(linked_resources.findall("link"))
    existing_names = set()
    for link in links:
        resource_name = link.findtext("name")
        if resource_name and not has_glob(resource_name):
            existing_names.add(resource_name.replace("\\", "/").lower())
    expanded_count = 0

    for link in links:
        name_element = link.find("name")
        if name_element is None or not name_element.text or not has_glob(name_element.text):
            continue

        resource_type = link.findtext("type")
        if resource_type == "2":
            linked_resources.remove(link)
            continue

        # link 名称是虚拟路径，实际文件要通过 link 的 location 查找。
        name_prefix, name_glob = split_glob(name_element.text.replace("\\", "/"))
        location = link.findtext("location")
        uri = (link.findtext("locationURI") or "").replace("\\", "/")
        uri_prefix, uri_glob = split_glob(uri)
        if location:
            fs_prefix, fs_glob = split_glob(location.replace("\\", "/"))
        elif uri and uri != "virtual:/virtual":
            fs_prefix, fs_glob = resolve_location_uri(uri_prefix, project_dir), uri_glob
        else:
            fs_prefix, fs_glob = os.path.join(root_dir, name_prefix), name_glob
        matches = recursive_glob(os.path.join(fs_prefix, fs_glob))
        if resource_type == "1":
            matches = [match for match in matches if os.path.isfile(match)]

        insert_at = list(linked_resources).index(link)
        for match in sorted(matches, key=str.lower):
            relative_path = os.path.relpath(match, fs_prefix).replace("\\", "/")
            resource_name = "/".join(part for part in (name_prefix, relative_path) if part)
            if resource_name.lower() in existing_names:
                continue

            missing_folders = []
            parent = posixpath.dirname(resource_name)
            while parent and parent.lower() not in existing_names:
                missing_folders.append(parent)
                parent = posixpath.dirname(parent)
            for folder in reversed(missing_folders):
                linked_resources.insert(insert_at, virtual_folder(folder))
                insert_at += 1
                existing_names.add(folder.lower())

            expanded_link = copy.deepcopy(link)
            expanded_link.find("name").text = resource_name
            location_uri = expanded_link.find("locationURI")
            if location_uri is not None and uri and uri != "virtual:/virtual":
                location_uri.text = "/".join(part for part in (uri_prefix, relative_path) if part)
            location_element = expanded_link.find("location")
            if location_element is not None:
                location_element.text = os.path.normpath(match)

            linked_resources.insert(insert_at, expanded_link)
            insert_at += 1
            existing_names.add(resource_name.lower())
            expanded_count += 1

        if not matches:
            logging.warning("No project resources matched %s", name_element.text)
        linked_resources.remove(link)

    ET.indent(tree, space="  ")
    tree.write(project_file, encoding="UTF-8", xml_declaration=True)
    return expanded_count


def normalize_entry(entry, root_dir, build_dir_name, oem_path):
    """把条目改为相对 workspace 的路径，并补上缺失的 'tresos' 目录层级。"""
    line = entry if os.path.isabs(entry) else os.path.join(root_dir, entry)
    line = os.path.normpath(line)

    if line.lower().startswith(root_dir.lower() + os.sep):
        line = line[len(root_dir) + 1:]

    if oem_path:
        match = re.search(r"(%s\\.*)" % re.escape(oem_path), line, flags=re.IGNORECASE)
        if match:
            line = match.group(1).lstrip("\\/")

    parts = line.replace("/", "\\").split("\\")
    if build_dir_name in parts:
        insert_idx = parts.index(build_dir_name) + 1
        if insert_idx < len(parts) and parts[insert_idx].lower() != "tresos":
            parts.insert(insert_idx, "tresos")
        line = "\\".join(parts)
    return line


def build_entries(raw_lines, args, root_dir, search_bases):
    build_dir_name = os.path.basename(os.path.normpath(args.build_dir))
    oem_path = os.path.normpath(args.oem_path) if args.oem_path else None
    excludes = [re.compile(pattern, flags=re.IGNORECASE) for pattern in args.exclude_patterns]

    entries = []
    seen = set()
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        for candidate in expand_globs(raw, search_bases):
            line = normalize_entry(candidate, root_dir, build_dir_name, oem_path)
            if any(exclude.match(line) for exclude in excludes):
                continue
            key = line.lower()
            if key not in seen:
                seen.add(key)
                entries.append(line)
    return entries


def main(argv=None):
    logging.basicConfig(format="[%(levelname)s] : %(message)s", level=logging.INFO)
    args = parse_args(argv)

    root_dir = os.path.normpath(os.getcwd())
    # PrefSystemImporter 把每个输入与 --base-path 拼接，并在 AB workspace 中运行，
    # 因此传给它的路径都必须是绝对路径。
    base_path = os.path.abspath(os.path.join(args.variant_dir, "tresos"))
    args.xdm_files = [os.path.abspath(path) for path in args.xdm_files]
    args.build_dir = os.path.abspath(args.build_dir)
    output_dir = os.path.join(args.tools_dir, "autosar_builder", "output")
    os.makedirs(output_dir, exist_ok=True)
    list_file = os.path.join(output_dir, "%s.lst" % args.variant)

    if not gen_ab_project(args, base_path):
        return 1
    project_file = os.path.join(
        args.tools_dir, "autosar_builder", "workspace", args.variant, ".project")
    expanded_count = expand_project_globs(project_file, root_dir)
    logging.info("Expanded %d wildcard project resources", expanded_count)
    if not gen_file_list(args, base_path, list_file):
        return 1

    with open(list_file, "r", encoding="utf-8") as file_handle:
        raw_lines = file_handle.readlines()

    entries = build_entries(raw_lines, args, root_dir, [None, root_dir, base_path])

    targets = [
        list_file,
        os.path.join(output_dir, "%s.list" % args.variant),
        os.path.join(output_dir, "%s_in_arxml_path.txt" % args.variant),
    ]
    for target in targets:
        with open(target, "w", encoding="utf-8") as file_handle:
            file_handle.write("\n".join(entries) + "\n")
        logging.info("Generated %s (%d entries)", target, len(entries))

    return 0


if __name__ == "__main__":
    sys.exit(main())