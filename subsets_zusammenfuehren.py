#!/usr/bin/env python3

import sys
import os
import struct
import copy
import xml.etree.ElementTree as ET
from collections import defaultdict, namedtuple

Subset = namedtuple("Subset", ["node", "verts", "tris"])

facestruct = struct.Struct("1H1H1H")
LSB_SEPARATOR = facestruct.pack(0, 0, 0)
XML_SEPARATOR = ET.Element("Face", {"i": "0;0;0"})


def calc_subset_key(subset_node):
    result = ""
    for key, value in sorted(subset_node.attrib.items()):
        if key in ("MeshI", "MeshV"):
            continue
        result += f"{key}={value},"
    for child in subset_node:
        if child.tag in ("Vertex", "Face"):
            continue
        result += f"{child.tag}=[" + calc_subset_key(child) + "]"
    return result


def pack(subsets, orig_lsb_data):
    vert_idx_offset = 0
    new_vertex_data = b"" if orig_lsb_data is not None else []
    new_tri_data = b"" if orig_lsb_data is not None else []
    new_lsb_data = b"" if orig_lsb_data is not None else None
    new_subset_nodes = []

    def flush():
        nonlocal vert_idx_offset, new_vertex_data, new_tri_data, orig_lsb_data, new_lsb_data
        if orig_lsb_data is not None:
            new_node = copy.deepcopy(subsets[0].node)
            new_node.attrib["MeshV"] = str(len(new_vertex_data) // 40)
            new_node.attrib["MeshI"] = str(len(new_tri_data) // 2)
            new_lsb_data += new_vertex_data
            new_lsb_data += new_tri_data
            new_vertex_data = b""
            new_tri_data = b""
        else:
            new_node = ET.Element(
                subsets[0].node.tag, copy.deepcopy(subsets[0].node.attrib)
            )
            new_node[:] = (
                [
                    copy.deepcopy(node)
                    for node in subsets[0].node
                    if node.tag not in ("Vertex", "Face")
                ]
                + new_vertex_data
                + new_tri_data
            )
            new_vertex_data = []
            new_tri_data = []
        new_subset_nodes.append(new_node)
        vert_idx_offset = 0

    for subset in subsets:
        num_vertices = (
            ((subset.verts.stop - subset.verts.start) // 40)
            if orig_lsb_data is not None
            else len(subset.verts)
        )
        if vert_idx_offset + num_vertices >= 32768:
            flush()
        assert vert_idx_offset + num_vertices < 32768
        new_vertex_data += (
            orig_lsb_data[subset.verts] if orig_lsb_data is not None else subset.verts
        )
        if vert_idx_offset == 0:
            new_tri_data += (
                orig_lsb_data[subset.tris] if orig_lsb_data is not None else subset.tris
            )
        else:
            if orig_lsb_data is not None:
                new_tri_data += LSB_SEPARATOR
                for faceindexes in facestruct.iter_unpack(orig_lsb_data[subset.tris]):
                    new_tri_data += facestruct.pack(
                        faceindexes[0] + vert_idx_offset,
                        faceindexes[1] + vert_idx_offset,
                        faceindexes[2] + vert_idx_offset,
                    )
            else:
                new_tri_data.append(copy.deepcopy(XML_SEPARATOR))
                for face_node in subset.tris:
                    faceindexes = face_node.attrib["i"].split(";")
                    face_node.attrib["i"] = ";".join(
                        (
                            str(int(faceindexes[0]) + vert_idx_offset),
                            str(int(faceindexes[1]) + vert_idx_offset),
                            str(int(faceindexes[2]) + vert_idx_offset),
                        )
                    )
                new_tri_data += subset.tris
        vert_idx_offset += num_vertices
    flush()
    return (new_subset_nodes, new_lsb_data)


def unpack(subset, orig_lsb_data):
    vert_idx_offset = 0
    new_subset_nodes = []
    if orig_lsb_data is not None:
        new_lsb_data = b""
        lsb_data_start = subset.tris.start
        for lsb_data_end in range(subset.tris.start, subset.tris.stop + 6, 6):
            if (lsb_data_end != subset.tris.stop) and (
                orig_lsb_data[lsb_data_end : lsb_data_end + 6] != LSB_SEPARATOR
            ):
                continue
            # Determine all vertices which must be extracted.
            facedata = list(
                facestruct.iter_unpack(orig_lsb_data[lsb_data_start:lsb_data_end])
            )
            max_vertex_index = max(max(faceindexes) for faceindexes in facedata)
            assert max_vertex_index < (subset.verts.stop - subset.verts.start)

            new_lsb_data += orig_lsb_data[
                (subset.verts.start + 40 * vert_idx_offset) : (
                    subset.verts.start + 40 * (max_vertex_index + 1)
                )
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
        return (new_subset_nodes, new_lsb_data)
    else:
        tri_idx_start = 0
        for tri_idx_end in range(len(subset.tris) + 1):
            if (tri_idx_end != len(subset.tris)) and (
                subset.tris[tri_idx_end].attrib["i"] != "0;0;0"
            ):
                continue
            # Determine all vertices which must be extracted.
            facedata = [
                [int(i) for i in face_node.attrib["i"].split(";")]
                for face_node in subset.tris[tri_idx_start:tri_idx_end]
            ]
            max_vertex_index = max(max(faceindexes) for faceindexes in facedata)
            assert max_vertex_index < len(subset.verts)

            new_node = ET.Element(subset.node.tag, copy.deepcopy(subset.node.attrib))
            new_node[:] = [
                copy.deepcopy(node)
                for node in subset.node
                if node.tag not in ("Vertex", "Face")
            ] + subset.verts[vert_idx_offset : max_vertex_index + 1]
            for faceindexes in facedata:
                ET.SubElement(
                    new_node,
                    "Face",
                    {
                        "i": ";".join(
                            str(faceindex - vert_idx_offset)
                            for faceindex in faceindexes
                        )
                    },
                )
            new_subset_nodes.append(new_node)

            vert_idx_offset = max_vertex_index + 1
            tri_idx_start = tri_idx_end + 1
        return (new_subset_nodes, None)


if sys.argv[1] not in ("pack", "unpack", "dry_pack", "dry_unpack"):
    print(f"Syntax: {sys.argv[0]} {pack|unpack|dry_pack|dry_unpack} datei1 ... dateiN")
    sys.exit(1)

for ls3_filename in sys.argv[2:]:
    print(f".ls3-Datei: {ls3_filename}")
    subsets_by_key = defaultdict(list)
    lsb_offset = 0
    root = ET.parse(ls3_filename).getroot()
    landschaft_node = root.find("./Landschaft")
    subset_nodes = list(landschaft_node.findall("./SubSet"))
    if not subset_nodes:
        print("Keine Subsets")
        continue

    lsb_filename = os.path.splitext(ls3_filename)[0] + ".lsb"
    try:
        lsb_data = open(lsb_filename, "rb").read()
        print(f".lsb-Datei: {lsb_filename}")
    except FileNotFoundError:
        lsb_data = None
        print(f"Keine .lsb-Datei")

    for subset_node in subset_nodes:
        if lsb_data is not None:
            num_vertices = int(subset_node.get("MeshV", 0))
            num_tris = int(subset_node.get("MeshI", 0))
            tris_offset = lsb_offset + num_vertices * 40
            lsb_offset_neu = tris_offset + num_tris * 2
            subsets_by_key[calc_subset_key(subset_node)].append(
                Subset(
                    subset_node,
                    slice(lsb_offset, tris_offset),
                    slice(tris_offset, lsb_offset_neu),
                )
            )
            lsb_offset = lsb_offset_neu
        else:
            subsets_by_key[calc_subset_key(subset_node)].append(
                Subset(
                    subset_node,
                    subset_node.findall("./Vertex"),
                    subset_node.findall("./Face"),
                )
            )
    if lsb_data:
        assert lsb_offset == len(
            lsb_data
        ), "Größe der .lsb-Datei passt nicht zu .ls3-Datei"

    new_lsb_data = b"" if lsb_data is not None else None
    new_subset_nodes = []
    for subsets in subsets_by_key.values():
        if sys.argv[1] in ("pack", "dry_pack"):
            (packed_subsets, packed_lsb_data) = pack(subsets, lsb_data)
            new_subset_nodes += packed_subsets
            if new_lsb_data is not None:
                new_lsb_data += packed_lsb_data
        else:
            for subset in subsets:
                (unpacked_subsets, unpacked_lsb_data) = unpack(subset, lsb_data)
                new_subset_nodes += unpacked_subsets
                if new_lsb_data is not None:
                    new_lsb_data += unpacked_lsb_data
    if new_lsb_data is not None:
        assert len(new_lsb_data) == sum(
            40 * int(subset_node.attrib["MeshV"]) + 2 * int(subset_node.attrib["MeshI"])
            for subset_node in new_subset_nodes
        )
    if len(subset_nodes) == len(new_subset_nodes):
        print(f"Keine Änderungen")
    else:
        print(f"{len(subset_nodes)} -> {len(new_subset_nodes)} Subsets")

        if sys.argv[1].startswith("dry_"):
            print("Änderungen werden nicht gespeichert.")
        else:
            landschaft_node[:] = new_subset_nodes + [
                node for node in landschaft_node if node.tag != "SubSet"
            ]

            with open(ls3_filename + "~", "wb") as f:
                f.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
            if new_lsb_data is not None:
                with open(lsb_filename + "~", "wb") as f:
                    f.write(new_lsb_data)
                os.rename(lsb_filename + "~", lsb_filename)
            os.rename(ls3_filename + "~", ls3_filename)
