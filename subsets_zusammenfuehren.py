#!/usr/bin/env python3

import sys
import os
import struct
import copy
import xml.etree.ElementTree as ET
from collections import defaultdict, namedtuple

Subset = namedtuple("Subset", ["node", "verts", "tris", "is_composite"])

facestruct = struct.Struct("1H1H1H")
SEPARATOR = facestruct.pack(0, 0, 0)


def calc_subset_key(subset_node):
    result = ""
    for key, value in sorted(subset_node.attrib.items()):
        if key in ("MeshI", "MeshV"):
            continue
        result += f"{key}={value},"
    for child in subset_node:
        result += f"{child.tag}=[" + calc_subset_key(child) + "]"
    return result


def pack(subsets, orig_lsb_data):
    vert_idx_offset = 0
    new_vertex_data = b""
    new_tri_data = b""
    new_lsb_data = b""
    new_subset_nodes = []

    def flush():
        nonlocal vert_idx_offset, new_vertex_data, new_tri_data, new_lsb_data
        new_node = copy.deepcopy(subsets[0].node)
        new_node.attrib["MeshV"] = str(len(new_vertex_data) // 40)
        new_node.attrib["MeshI"] = str(len(new_tri_data) // 2)
        new_subset_nodes.append(new_node)
        vert_idx_offset = 0
        new_lsb_data += new_vertex_data
        new_lsb_data += new_tri_data
        new_vertex_data = b""
        new_tri_data = b""

    for subset in subsets:
        new_vertex_data += orig_lsb_data[subset.verts]
        new_vert_idx_offset = (
            vert_idx_offset + (subset.verts.stop - subset.verts.start) // 40
        )
        if new_vert_idx_offset >= 32768:
            flush()
            new_vert_idx_offset = (
                vert_idx_offset + (subset.verts.stop - subset.verts.start) // 40
            )
        if vert_idx_offset == 0:
            new_tri_data += orig_lsb_data[subset.tris]
        else:
            new_tri_data += SEPARATOR
            for faceindexes in facestruct.iter_unpack(orig_lsb_data[subset.tris]):
                new_tri_data += facestruct.pack(
                    faceindexes[0] + vert_idx_offset,
                    faceindexes[1] + vert_idx_offset,
                    faceindexes[2] + vert_idx_offset,
                )
        vert_idx_offset = new_vert_idx_offset
    flush()
    return (new_subset_nodes, new_lsb_data)


def unpack(subset, orig_lsb_data):
    vert_idx_offset = 0
    new_subset_nodes = []
    new_lsb_data = b""
    lsb_data_start = subset.tris.start
    lsb_data_end = lsb_data_start
    while lsb_data_end <= subset.tris.stop:
        if (lsb_data_end == subset.tris.stop) or (
            orig_lsb_data[lsb_data_end : lsb_data_end + 6] == SEPARATOR
        ):
            # Determine all vertices which must be extracted.
            facedata = list(
                facestruct.iter_unpack(orig_lsb_data[lsb_data_start:lsb_data_end])
            )
            max_vertex_index = max(max(faceindexes) for faceindexes in facedata)
            assert max_vertex_index < (subset.verts.stop - subset.verts.start)
            new_lsb_data += orig_lsb_data[
                subset.verts.start
                + 40 * vert_idx_offset : subset.verts.start
                + 40 * (max_vertex_index + 1)
            ]
            for faceindexes in facedata:
                new_lsb_data += facestruct.pack(
                    faceindexes[0] - vert_idx_offset,
                    faceindexes[1] - vert_idx_offset,
                    faceindexes[2] - vert_idx_offset,
                )

            new_node = copy.deepcopy(subset.node)
            new_node.attrib["MeshV"] = str(max_vertex_index - vert_idx_offset + 1)
            new_node.attrib["MeshI"] = str(len(facedata) * 3)
            new_subset_nodes.append(new_node)

            vert_idx_offset = max_vertex_index + 1
            lsb_data_start = lsb_data_end + 6
            lsb_data_end = lsb_data_start
        else:
            lsb_data_end += 6
    return (new_subset_nodes, new_lsb_data)


if sys.argv[1] not in ("pack", "unpack", "dry_pack", "dry_unpack"):
    sys.exit(1)

for ls3_filename in sys.argv[2:]:
    subsets_by_key = defaultdict(list)
    lsb_offset = 0
    root = ET.parse(ls3_filename).getroot()
    landschaft_node = root.find("./Landschaft")
    subset_nodes = list(landschaft_node.findall("./SubSet"))
    if not subset_nodes:
        print("Keine Subsets")
        continue

    lsb_filename = os.path.splitext(ls3_filename)[0] + ".lsb"
    lsb_data = open(lsb_filename, "rb").read()

    for subset_node in subset_nodes:
        num_vertices = int(subset_node.get("MeshV", 0))
        num_tris = int(subset_node.get("MeshI", 0))
        tris_offset = lsb_offset + num_vertices * 40
        lsb_offset_neu = tris_offset + num_tris * 2
        is_composite = any(
            lsb_data[offset : offset + 6] == SEPARATOR
            for offset in range(tris_offset, lsb_offset_neu, 6)
        )
        subsets_by_key[calc_subset_key(subset_node)].append(
            Subset(
                subset_node,
                slice(lsb_offset, tris_offset),
                slice(tris_offset, lsb_offset_neu),
                is_composite,
            )
        )
        lsb_offset = lsb_offset_neu
    assert lsb_offset == len(lsb_data), "Größe der .lsb-Datei passt nicht zu .ls3-Datei"

    new_lsb_data = b""
    new_subset_nodes = []
    for subsets in subsets_by_key.values():
        if sys.argv[1] in ("pack", "dry_pack"):
            (packed_subsets, packed_lsb_data) = pack(subsets, lsb_data)
            new_subset_nodes += packed_subsets
            new_lsb_data += packed_lsb_data
        else:
            for subset in subsets:
                (unpacked_subsets, unpacked_lsb_data) = unpack(subset, lsb_data)
                new_subset_nodes += unpacked_subsets
                new_lsb_data += unpacked_lsb_data
    assert len(new_lsb_data) == sum(
        40 * int(subset_node.attrib["MeshV"]) + 2 * int(subset_node.attrib["MeshI"])
        for subset_node in new_subset_nodes
    )
    print(f"{len(subset_nodes)} -> {len(new_subset_nodes)} Subsets")

    if sys.argv[1].startswith("dry_"):
        print("Änderungen werden nicht gespeichert")
    else:
        landschaft_node[:] = new_subset_nodes + [
            node for node in landschaft_node if node.tag != "SubSet"
        ]

        with open(ls3_filename + "~", "wb") as f:
            f.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        with open(lsb_filename + "~", "wb") as f:
            f.write(new_lsb_data)

        os.rename(ls3_filename + "~", ls3_filename)
        os.rename(lsb_filename + "~", lsb_filename)
