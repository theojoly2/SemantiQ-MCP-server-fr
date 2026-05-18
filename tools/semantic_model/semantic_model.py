from __future__ import annotations

import json
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, SKOS, URIRef, XSD

from resources.semantic_model.utils import MODELS_PATH


BASE_MODELS_PATH = Path(MODELS_PATH)
BASE_MODELS_PATH.mkdir(parents=True, exist_ok=True)

UML_META = Namespace("urn:ai4semantics:uml:")


def ea_id(prefix: str, uri: str) -> str:
    u = uuid.uuid5(uuid.NAMESPACE_URL, str(uri))
    s = str(u).replace("-", "_").upper()
    return f"{prefix}_{s}"


def local_name(uri: URIRef | str) -> str:
    s = str(uri)
    if "#" in s:
        return s.split("#")[-1]
    return s.rstrip("/").split("/")[-1]


def _norm_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def _safe_xmi_value(value: Any) -> str:
    value = _norm_text(value)
    return value if value is not None else ""


def _canonical_text(value: Any) -> str:
    value = _norm_text(value) or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return value


def _slugify_uri(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "generated"


def _sanitize_path_component(value: str | None, default: str) -> str:
    value = _norm_text(value) or default
    value = value.replace("\\", "_").replace("/", "_").replace("\x00", "")
    return value.strip() or default


def _find_file(user: str, name: str) -> Path:
    safe_user = _sanitize_path_component(user, "default")
    safe_name = _sanitize_path_component(name, "generated")
    return BASE_MODELS_PATH / safe_user / f"{safe_name}.json"


def get_model_path(user: str = "", name: str = "") -> str:
    return str(_find_file(user, name))


def _model_uri(user: str, name: str) -> URIRef:
    return URIRef(
        f"urn:ai4semantics:model:{_slugify_uri(user or 'default')}:{_slugify_uri(name or 'generated')}"
    )


def _new_graph(user: str, name: str) -> Graph:
    g = Graph()
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    g.bind("skos", SKOS)
    g.bind("uml", UML_META)

    onto = _model_uri(user, name)
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, RDFS.label, Literal(name or "Generated", lang="fr")))
    return g


def _first_literal(
    g: Graph,
    s: URIRef,
    p: URIRef,
    preferred_langs: tuple[str, ...] = ("fr", "en"),
) -> str | None:
    vals = list(g.objects(s, p))
    if not vals:
        return None

    for lang in preferred_langs:
        for v in vals:
            if isinstance(v, Literal) and v.language == lang:
                return str(v)

    for v in vals:
        if isinstance(v, Literal):
            return str(v)

    return str(vals[0])


def get_label(g: Graph, s: URIRef) -> str | None:
    return _first_literal(g, s, RDFS.label)


def get_comment(g: Graph, s: URIRef) -> str | None:
    return _first_literal(g, s, RDFS.comment)


def get_scope_note(g: Graph, s: URIRef) -> str | None:
    return _first_literal(g, s, SKOS.scopeNote)


def get_literal_value(g: Graph, s: URIRef, p: URIRef) -> str | None:
    vals = list(g.objects(s, p))
    if not vals:
        return None
    return str(vals[0])


def _set_literal(
    g: Graph,
    s: URIRef,
    p: URIRef,
    value: str | None,
    lang: str | None = None,
) -> None:
    g.remove((s, p, None))
    value = _norm_text(value)
    if value is not None:
        if lang:
            g.add((s, p, Literal(value, lang=lang)))
        else:
            g.add((s, p, Literal(value)))


def _set_literal_if_absent(
    g: Graph,
    s: URIRef,
    p: URIRef,
    value: str | None,
    lang: str | None = None,
) -> None:
    if list(g.objects(s, p)):
        return
    value = _norm_text(value)
    if value is not None:
        if lang:
            g.add((s, p, Literal(value, lang=lang)))
        else:
            g.add((s, p, Literal(value)))


def _add_literal(
    g: Graph,
    s: URIRef,
    p: URIRef,
    value: str | None,
    lang: str | None = None,
) -> None:
    value = _norm_text(value)
    if value is not None:
        if lang:
            g.add((s, p, Literal(value, lang=lang)))
        else:
            g.add((s, p, Literal(value)))


def _set_uri(g: Graph, s: URIRef, p: URIRef, value: URIRef | str | None) -> None:
    g.remove((s, p, None))
    if value:
        g.add((s, p, URIRef(str(value))))


def _add_uri(g: Graph, s: URIRef, p: URIRef, value: URIRef | str | None) -> None:
    if value:
        g.add((s, p, URIRef(str(value))))


def _coerce_class_uri(value: str | None, fallback_label: str) -> URIRef:
    value = _norm_text(value)
    if value and (value.startswith("http://") or value.startswith("https://") or value.startswith("urn:")):
        return URIRef(value)
    if value:
        return URIRef(f"urn:eaid:{value}")
    return URIRef(f"urn:class:{_slugify_uri(fallback_label)}")


def primitive_from_range(r: URIRef) -> str | None:
    s = str(r)
    if s in (str(RDFS.Literal), str(RDF.langString)):
        return "String"

    xsd_map = {
        str(XSD.string): "String",
        str(XSD.boolean): "Boolean",
        str(XSD.integer): "Integer",
        str(XSD.int): "Integer",
        str(XSD.long): "Integer",
        str(XSD.float): "Real",
        str(XSD.double): "Real",
        str(XSD.decimal): "Real",
        str(XSD.date): "Date",
        str(XSD.dateTime): "DateTime",
        str(XSD.time): "Time",
        str(XSD.anyURI): "URI",
    }
    return xsd_map.get(s)


_FRIENDLY_TO_XSD_URI: dict[str, str] = {
    "string": str(XSD.string),
    "text": str(RDF.langString),
    "literal": str(RDFS.Literal),
    "boolean": str(XSD.boolean),
    "integer": str(XSD.integer),
    "int": str(XSD.int),
    "long": str(XSD.long),
    "float": str(XSD.float),
    "real": str(XSD.double),
    "double": str(XSD.double),
    "decimal": str(XSD.decimal),
    "number": str(XSD.decimal),
    "date": str(XSD.date),
    "datetime": str(XSD.dateTime),
    "time": str(XSD.time),
    "uri": str(XSD.anyURI),
    "xsd:string": str(XSD.string),
    "xsd:boolean": str(XSD.boolean),
    "xsd:integer": str(XSD.integer),
    "xsd:decimal": str(XSD.decimal),
    "xsd:float": str(XSD.float),
    "xsd:double": str(XSD.double),
    "xsd:date": str(XSD.date),
    "xsd:dateTime": str(XSD.dateTime),
    "xsd:anyURI": str(XSD.anyURI),
    "rdf:langString": str(RDF.langString),
    "rdfs:Literal": str(RDFS.Literal),
}


def resolve_type_uri(attr_type: str | None) -> tuple[str, bool]:
    attr_type = _norm_text(attr_type)
    if not attr_type:
        return ("", False)

    if "://" in attr_type or attr_type.startswith("urn:"):
        prim = primitive_from_range(URIRef(attr_type))
        return (attr_type, prim is not None)

    canonical = _FRIENDLY_TO_XSD_URI.get(attr_type) or _FRIENDLY_TO_XSD_URI.get(attr_type.lower())
    if canonical:
        return (canonical, True)

    return (attr_type, False)


def _default_source_format(model: dict[str, Any]) -> str:
    fmt = _norm_text(model.get("source_format"))
    if fmt:
        return fmt.lower()

    if model.get("ttl_raw"):
        return "ttl"
    return "xmi"


def extract_ontology_package(g: Graph, custom_name: str | None = None) -> dict[str, Any]:
    ontos = list(g.subjects(RDF.type, OWL.Ontology))
    if ontos:
        onto = ontos[0]
        name = custom_name or get_label(g, onto) or local_name(onto)
        pkg_uri = str(onto)
    else:
        name = custom_name or "Generated"
        pkg_uri = f"urn:pkg:{name}"

    return {
        "name": name,
        "ID": ea_id("EAPK", pkg_uri),
        "type": "uml:Package",
        "package": "",
        "tags": [],
    }


def _first_tag_value(tags: list[dict[str, Any]] | None, key: str) -> str:
    for tag in tags or []:
        if _norm_text(tag.get("name")) == key:
            return _norm_text(tag.get("value")) or ""
    return ""


def _xmi_view(model: dict[str, Any]) -> dict[str, Any]:
    xmi = model.get("xmi")
    if isinstance(xmi, dict):
        return {
            "elements": xmi.get("elements", []) or [],
            "connectors": xmi.get("connectors", []) or [],
        }
    return {
        "elements": model.get("elements", []) or [],
        "connectors": model.get("connectors", []) or [],
    }


def _class_uri_from_element(el: dict[str, Any]) -> URIRef:
    uri = _first_tag_value(el.get("tags", []), "uri")
    if uri:
        return URIRef(uri)

    el_id = _norm_text(el.get("ID"))
    if el_id:
        return URIRef(f"urn:eaid:{el_id}")

    return URIRef(
        f"urn:class:{_slugify_uri(_safe_xmi_value(el.get('name')) or 'generated')}"
    )


def _attribute_uri(class_uri: URIRef, attr: dict[str, Any]) -> URIRef:
    uri = _first_tag_value(attr.get("tags_attribute", []), "uri")
    if uri:
        return URIRef(uri)

    attr_name = _safe_xmi_value(attr.get("name")) or "attribute"
    return URIRef(f"{str(class_uri)}#{_slugify_uri(attr_name)}")


def _semantic_uri_from_connector_view(conn: dict[str, Any]) -> str:
    return (
        _norm_text(conn.get("semantic_uri"))
        or _first_tag_value(conn.get("tags", []), "semantic_uri")
        or _first_tag_value(conn.get("tags_target", []), "semantic_uri")
        or _first_tag_value(conn.get("tags_source", []), "semantic_uri")
        or _norm_text(conn.get("uri"))
        or _first_tag_value(conn.get("tags", []), "uri")
        or _first_tag_value(conn.get("tags_target", []), "uri")
        or _first_tag_value(conn.get("tags_source", []), "uri")
        or ""
    )


def _connector_id_from_view(conn: dict[str, Any]) -> str:
    return (
        _norm_text(conn.get("connector_id"))
        or _first_tag_value(conn.get("tags", []), "connector_id")
        or _first_tag_value(conn.get("tags_target", []), "connector_id")
        or _first_tag_value(conn.get("tags_source", []), "connector_id")
        or ""
    )


def _connector_key_from_parts(
    semantic_uri: str,
    relationship: str,
    source_uri: str,
    target_uri: str,
) -> str:
    return "§".join(
        [
            _norm_text(semantic_uri) or "",
            _norm_text(relationship) or "",
            _norm_text(source_uri) or "",
            _norm_text(target_uri) or "",
        ]
    )


def _build_connector_id(
    semantic_uri: str,
    relationship: str,
    source_uri: str,
    target_uri: str,
) -> str:
    return ea_id(
        "CONN",
        _connector_key_from_parts(semantic_uri, relationship, source_uri, target_uri),
    )


def _resolve_class_uri_from_ref(
    *,
    ref_id: str = "",
    ref_name: str = "",
    class_uri_by_id: dict[str, URIRef],
    class_uri_by_name: dict[str, URIRef],
) -> URIRef | None:
    ref_id = _norm_text(ref_id) or ""
    ref_name = _norm_text(ref_name) or ""

    if ref_id and ref_id in class_uri_by_id:
        return class_uri_by_id[ref_id]

    if ref_name:
        found = class_uri_by_name.get(_canonical_text(ref_name))
        if found:
            return found

    if ref_id:
        return URIRef(f"urn:eaid:{ref_id}")

    if ref_name:
        return URIRef(f"urn:class:{_slugify_uri(ref_name)}")

    return None


def _extract_connector_overrides(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    xmi = _xmi_view(model)
    elements = xmi.get("elements", [])
    connectors = xmi.get("connectors", [])

    class_uri_by_id: dict[str, str] = {}
    class_uri_by_name: dict[str, str] = {}

    for el in elements:
        if _norm_text(el.get("type")) != "uml:Class":
            continue

        class_uri = str(_class_uri_from_element(el))
        el_id = _safe_xmi_value(el.get("ID"))
        el_name = _safe_xmi_value(el.get("name"))

        if el_id:
            class_uri_by_id[el_id] = class_uri
        if el_name:
            class_uri_by_name[_canonical_text(el_name)] = class_uri

    overrides: dict[str, dict[str, Any]] = {}

    for conn in connectors:
        relationship = _norm_text(conn.get("relationship")) or "Association"

        source_uri = (
            class_uri_by_id.get(_safe_xmi_value(conn.get("source_id")))
            or class_uri_by_name.get(_canonical_text(conn.get("source_name")))
            or ""
        )
        target_uri = (
            class_uri_by_id.get(_safe_xmi_value(conn.get("target_id")))
            or class_uri_by_name.get(_canonical_text(conn.get("target_name")))
            or ""
        )

        semantic_uri = _semantic_uri_from_connector_view(conn)
        if relationship == "Generalization":
            semantic_uri = ""

        if not source_uri or not target_uri:
            continue

        key = _connector_key_from_parts(semantic_uri, relationship, source_uri, target_uri)
        connector_id = _connector_id_from_view(conn) or _build_connector_id(
            semantic_uri,
            relationship,
            source_uri,
            target_uri,
        )

        overrides[key] = {
            "connector_id": connector_id,
            "semantic_uri": semantic_uri,
            "relationship": relationship,
            "name": _safe_xmi_value(conn.get("name")),
            "lb": _safe_xmi_value(conn.get("lb")),
            "lt": _safe_xmi_value(conn.get("lt")),
            "rb": _safe_xmi_value(conn.get("rb")),
            "rt": _safe_xmi_value(conn.get("rt")),
            "source_name": _safe_xmi_value(conn.get("source_name")),
            "target_name": _safe_xmi_value(conn.get("target_name")),
        }

    return overrides


def build_model(
    g: Graph,
    package_name: str | None = None,
    connector_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model: dict[str, Any] = {"elements": [], "connectors": []}
    connector_overrides = connector_overrides or {}

    root_pkg = extract_ontology_package(g, custom_name=package_name)
    model["elements"].append(root_pkg)
    root_pkg_id = root_pkg["ID"]

    class_elems: dict[str, dict[str, Any]] = {}

    for cls in g.subjects(RDF.type, OWL.Class):
        uri = str(cls)
        name = get_label(g, cls) or local_name(cls)
        stored_id = get_literal_value(g, cls, UML_META.eaid) or ea_id("EAID", uri)

        tags = [
            {"name": "uri", "value": uri},
            {"name": "referenced", "value": "false"},
        ]
        definition = get_comment(g, cls)
        usage_note = get_scope_note(g, cls)
        label = get_label(g, cls)

        if definition:
            tags.append({"name": "definition-en", "value": definition})
        if label:
            tags.append({"name": "label-en", "value": label})
        if usage_note:
            tags.append({"name": "usageNote-en", "value": usage_note})

        elem = {
            "name": name,
            "ID": stored_id,
            "type": "uml:Class",
            "package": root_pkg_id,
            "tags": tags,
            "attributes": [],
        }
        class_elems[uri] = elem
        model["elements"].append(elem)

    def ensure_class(uri_ref: URIRef) -> dict[str, Any]:
        u = str(uri_ref)
        if primitive_from_range(uri_ref) is not None:
            raise ValueError(f"Primitive URI passed to ensure_class: {u}")

        if u not in class_elems:
            name = get_label(g, uri_ref) or local_name(uri_ref)
            stored_id = get_literal_value(g, uri_ref, UML_META.eaid) or ea_id("EAID", u)
            elem = {
                "name": name,
                "ID": stored_id,
                "type": "uml:Class",
                "package": root_pkg_id,
                "tags": [
                    {"name": "uri", "value": u},
                    {"name": "referenced", "value": "true"},
                ],
                "attributes": [],
            }
            class_elems[u] = elem
            model["elements"].append(elem)

        return class_elems[u]

    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        prop_uri = str(prop)
        prop_label = get_label(g, prop) or local_name(prop)
        prop_comment = get_comment(g, prop)
        prop_usage = get_scope_note(g, prop)

        for domain in g.objects(prop, RDFS.domain):
            domain_elem = ensure_class(domain)
            ranges = list(g.objects(prop, RDFS.range)) or [RDFS.Literal]

            for rng in ranges:
                primitive = primitive_from_range(rng)
                attr_type = primitive if primitive else ensure_class(rng)["name"]

                attr_tags = [{"name": "uri", "value": prop_uri}]
                if prop_label:
                    attr_tags.append({"name": "label-en", "value": prop_label})
                if prop_comment:
                    attr_tags.append({"name": "definition-en", "value": prop_comment})
                if prop_usage:
                    attr_tags.append({"name": "usageNote-en", "value": prop_usage})

                domain_elem["attributes"].append(
                    {
                        "name": prop_label,
                        "type": attr_type,
                        "lower_bounds": "",
                        "upper_bounds": "",
                        "tags_attribute": attr_tags,
                    }
                )

    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        prop_uri = str(prop)
        prop_label = get_label(g, prop) or local_name(prop)
        prop_comment = get_comment(g, prop)
        prop_usage = get_scope_note(g, prop)

        domains = list(g.objects(prop, RDFS.domain))
        ranges = list(g.objects(prop, RDFS.range))
        if not domains or not ranges:
            continue

        relationship = get_literal_value(g, prop, UML_META.relationshipType) or "Association"
        default_lb = _safe_xmi_value(get_literal_value(g, prop, UML_META.leftMultiplicity))
        default_lt = _safe_xmi_value(get_literal_value(g, prop, UML_META.leftRole))
        default_rb = _safe_xmi_value(get_literal_value(g, prop, UML_META.rightMultiplicity))
        default_rt = _safe_xmi_value(get_literal_value(g, prop, UML_META.rightRole))

        for domain in domains:
            src = ensure_class(domain)
            for rng in ranges:
                tgt = ensure_class(rng)
                key = _connector_key_from_parts(prop_uri, relationship, str(domain), str(rng))
                override = connector_overrides.get(key, {})
                connector_id = override.get("connector_id") or _build_connector_id(
                    prop_uri,
                    relationship,
                    str(domain),
                    str(rng),
                )

                tgt_tags = [
                    {"name": "uri", "value": prop_uri},
                    {"name": "semantic_uri", "value": prop_uri},
                    {"name": "connector_id", "value": connector_id},
                ]
                if prop_comment:
                    tgt_tags.append({"name": "definition-en", "value": prop_comment})
                if prop_label:
                    tgt_tags.append({"name": "label-en", "value": prop_label})
                if prop_usage:
                    tgt_tags.append({"name": "usageNote-en", "value": prop_usage})

                model["connectors"].append(
                    {
                        "connector_id": connector_id,
                        "semantic_uri": prop_uri,
                        "source_name": override.get("source_name") or src["name"],
                        "source_id": src["ID"],
                        "target_name": override.get("target_name") or tgt["name"],
                        "target_id": tgt["ID"],
                        "relationship": relationship,
                        "name": override.get("name") or prop_label,
                        "lb": override.get("lb", default_lb),
                        "lt": override.get("lt", default_lt),
                        "rb": override.get("rb", default_rb),
                        "rt": override.get("rt", default_rt),
                        "tags": [{"name": "connector_id", "value": connector_id}],
                        "tags_source": [],
                        "tags_target": tgt_tags,
                    }
                )

    for child, parent in g.subject_objects(RDFS.subClassOf):
        if not isinstance(parent, URIRef):
            continue

        child_elem = ensure_class(child)
        parent_elem = ensure_class(parent)
        relationship = "Generalization"
        key = _connector_key_from_parts("", relationship, str(child), str(parent))
        override = connector_overrides.get(key, {})
        connector_id = override.get("connector_id") or _build_connector_id(
            "",
            relationship,
            str(child),
            str(parent),
        )

        model["connectors"].append(
            {
                "connector_id": connector_id,
                "semantic_uri": "",
                "source_name": override.get("source_name") or child_elem["name"],
                "source_id": child_elem["ID"],
                "target_name": override.get("target_name") or parent_elem["name"],
                "target_id": parent_elem["ID"],
                "relationship": relationship,
                "name": override.get("name") or "subClassOf",
                "lb": override.get("lb", ""),
                "lt": override.get("lt", ""),
                "rb": override.get("rb", ""),
                "rt": override.get("rt", ""),
                "tags": [{"name": "connector_id", "value": connector_id}],
                "tags_source": [],
                "tags_target": [{"name": "connector_id", "value": connector_id}],
            }
        )

    return model


def _sync_model_from_graph(
    g: Graph,
    model: dict[str, Any],
    package_name: str | None = None,
    source_format: str | None = None,
    connector_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    json_ld_str = g.serialize(format="json-ld", indent=4)
    ttl_raw = g.serialize(format="turtle")
    xmi = build_model(
        g,
        package_name=package_name,
        connector_overrides=connector_overrides or _extract_connector_overrides(model),
    )

    if isinstance(json_ld_str, (bytes, bytearray)):
        json_ld_str = json_ld_str.decode("utf-8")

    model["ttl"] = json.loads(json_ld_str) if json_ld_str else {}
    model["ttl_raw"] = ttl_raw.decode("utf-8") if isinstance(ttl_raw, (bytes, bytearray)) else str(ttl_raw)
    model["xmi"] = xmi
    model["elements"] = xmi.get("elements", [])
    model["connectors"] = xmi.get("connectors", [])
    model["source_format"] = (source_format or model.get("source_format") or "ttl").lower()

    return model


def _save_model(fp: Path, model: dict[str, Any]) -> dict[str, Any]:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_model(fp: Path) -> dict[str, Any]:
    if not fp.exists():
        return {}

    try:
        with open(fp, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                return {}
            return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def ensure_model_exists(user: str = "", name: str = "") -> Path:
    fp = _find_file(user, name)
    if fp.exists():
        return fp

    g = _new_graph(user, name or "Generated")
    model: dict[str, Any] = {
        "elements": [],
        "connectors": [],
        "xmi": {"elements": [], "connectors": []},
        "ttl": {},
        "ttl_raw": "",
        "source_format": "ttl",
    }
    model = _sync_model_from_graph(g, model, package_name=name or "Generated", source_format="ttl")
    _save_model(fp, model)
    return fp


def build_graph_from_xmi_model(
    model: dict[str, Any],
    user: str = "",
    name: str = "",
) -> Graph:
    xmi = _xmi_view(model)
    elements = xmi.get("elements", [])
    connectors = xmi.get("connectors", [])

    package_name = (
        _norm_text(name)
        or _norm_text(model.get("name"))
        or next(
            (
                _norm_text(el.get("name"))
                for el in elements
                if _norm_text(el.get("type")) == "uml:Package" and _norm_text(el.get("name"))
            ),
            None,
        )
        or "Generated"
    )

    g = _new_graph(user, package_name)
    onto = next(g.subjects(RDF.type, OWL.Ontology), None)
    if isinstance(onto, URIRef):
        _set_literal(g, onto, RDFS.label, package_name, lang="fr")

    class_uri_by_id: dict[str, URIRef] = {}
    class_uri_by_name: dict[str, URIRef] = {}

    def ensure_class(uri: URIRef, label: str = "", eaid_value: str = "") -> URIRef:
        g.add((uri, RDF.type, OWL.Class))
        if label:
            _set_literal(g, uri, RDFS.label, label, lang="fr")
        if eaid_value:
            _set_literal(g, uri, UML_META.eaid, eaid_value)
        return uri

    for el in elements:
        el_type = _norm_text(el.get("type")) or ""
        if el_type not in {"uml:Class", "uml:DataType", "uml:Enumeration"}:
            continue

        class_uri = _class_uri_from_element(el)
        class_name = _safe_xmi_value(el.get("name"))
        class_id = _safe_xmi_value(el.get("ID"))

        label = _first_tag_value(el.get("tags", []), "label-en") or class_name
        definition = _first_tag_value(el.get("tags", []), "definition-en")
        usage_note = _first_tag_value(el.get("tags", []), "usageNote-en")

        ensure_class(class_uri, label=label or class_name, eaid_value=class_id)
        _set_literal(g, class_uri, RDFS.comment, definition, lang="fr")
        _set_literal(g, class_uri, SKOS.scopeNote, usage_note, lang="fr")

        if class_id:
            class_uri_by_id[class_id] = class_uri
        if class_name:
            class_uri_by_name[_canonical_text(class_name)] = class_uri
        if label:
            class_uri_by_name[_canonical_text(label)] = class_uri

    for el in elements:
        el_type = _norm_text(el.get("type")) or ""
        if el_type not in {"uml:Class", "uml:DataType", "uml:Enumeration"}:
            continue

        owner_uri = _class_uri_from_element(el)

        for attr in el.get("attributes", []) or []:
            prop_uri = _attribute_uri(owner_uri, attr)
            attr_name = _safe_xmi_value(attr.get("name"))
            attr_label = _first_tag_value(attr.get("tags_attribute", []), "label-en") or attr_name
            attr_definition = _first_tag_value(attr.get("tags_attribute", []), "definition-en")
            attr_usage = _first_tag_value(attr.get("tags_attribute", []), "usageNote-en")
            attr_type = _norm_text(attr.get("type")) or ""

            canonical_range_uri, is_primitive = resolve_type_uri(attr_type)

            g.remove((prop_uri, RDF.type, OWL.DatatypeProperty))
            g.remove((prop_uri, RDF.type, OWL.ObjectProperty))
            g.add((prop_uri, RDF.type, OWL.DatatypeProperty if is_primitive else OWL.ObjectProperty))

            _set_literal(g, prop_uri, RDFS.label, attr_label, lang="fr")
            _set_literal(g, prop_uri, RDFS.comment, attr_definition, lang="fr")
            _set_literal(g, prop_uri, SKOS.scopeNote, attr_usage, lang="fr")
            _add_uri(g, prop_uri, RDFS.domain, owner_uri)

            if canonical_range_uri:
                if is_primitive:
                    _set_uri(g, prop_uri, RDFS.range, canonical_range_uri)
                else:
                    target_uri = (
                        class_uri_by_id.get(canonical_range_uri)
                        or class_uri_by_name.get(_canonical_text(canonical_range_uri))
                    )

                    if target_uri is None:
                        if "://" in canonical_range_uri or canonical_range_uri.startswith("urn:"):
                            target_uri = URIRef(canonical_range_uri)
                        else:
                            target_uri = URIRef(f"urn:class:{_slugify_uri(canonical_range_uri)}")
                        ensure_class(target_uri, label=attr_type or canonical_range_uri)

                    _add_uri(g, prop_uri, RDFS.range, target_uri)

    for conn in connectors:
        relationship = _norm_text(conn.get("relationship")) or "Association"

        source_uri = _resolve_class_uri_from_ref(
            ref_id=_safe_xmi_value(conn.get("source_id")),
            ref_name=_safe_xmi_value(conn.get("source_name")),
            class_uri_by_id=class_uri_by_id,
            class_uri_by_name=class_uri_by_name,
        )
        target_uri = _resolve_class_uri_from_ref(
            ref_id=_safe_xmi_value(conn.get("target_id")),
            ref_name=_safe_xmi_value(conn.get("target_name")),
            class_uri_by_id=class_uri_by_id,
            class_uri_by_name=class_uri_by_name,
        )

        if source_uri is None or target_uri is None:
            continue

        ensure_class(
            source_uri,
            label=_safe_xmi_value(conn.get("source_name")) or local_name(source_uri),
            eaid_value=_safe_xmi_value(conn.get("source_id")),
        )
        ensure_class(
            target_uri,
            label=_safe_xmi_value(conn.get("target_name")) or local_name(target_uri),
            eaid_value=_safe_xmi_value(conn.get("target_id")),
        )

        if relationship == "Generalization":
            g.add((source_uri, RDFS.subClassOf, target_uri))
            continue

        semantic_uri = _semantic_uri_from_connector_view(conn)
        if not semantic_uri:
            semantic_uri = f"urn:relation:{_slugify_uri(_safe_xmi_value(conn.get('name')) or 'relation')}"

        prop_uri = URIRef(semantic_uri)
        rel_label = (
            _norm_text(conn.get("name"))
            or _first_tag_value(conn.get("tags_target", []), "label-en")
            or _first_tag_value(conn.get("tags", []), "label-en")
            or local_name(prop_uri)
        )
        rel_definition = (
            _first_tag_value(conn.get("tags_target", []), "definition-en")
            or _first_tag_value(conn.get("tags", []), "definition-en")
        )
        rel_usage = (
            _first_tag_value(conn.get("tags_target", []), "usageNote-en")
            or _first_tag_value(conn.get("tags", []), "usageNote-en")
        )

        g.remove((prop_uri, RDF.type, OWL.DatatypeProperty))
        g.add((prop_uri, RDF.type, OWL.ObjectProperty))

        _set_literal(g, prop_uri, RDFS.label, rel_label, lang="fr")
        _set_literal(g, prop_uri, RDFS.comment, rel_definition, lang="fr")
        _set_literal(g, prop_uri, SKOS.scopeNote, rel_usage, lang="fr")
        _add_uri(g, prop_uri, RDFS.domain, source_uri)
        _add_uri(g, prop_uri, RDFS.range, target_uri)

        _set_literal(g, prop_uri, UML_META.relationshipType, relationship)

        _set_literal_if_absent(g, prop_uri, UML_META.leftMultiplicity, _safe_xmi_value(conn.get("lb")))
        _set_literal_if_absent(g, prop_uri, UML_META.rightMultiplicity, _safe_xmi_value(conn.get("rb")))
        _set_literal_if_absent(g, prop_uri, UML_META.leftRole, _safe_xmi_value(conn.get("lt")))
        _set_literal_if_absent(g, prop_uri, UML_META.rightRole, _safe_xmi_value(conn.get("rt")))

    return g


def _graph_from_model(model: dict[str, Any], user: str = "", name: str = "") -> Graph:
    ttl_raw = _norm_text(model.get("ttl_raw"))
    if ttl_raw:
        g = Graph()
        g.parse(data=ttl_raw, format="turtle")
        return g

    xmi = _xmi_view(model)
    if xmi.get("elements") or xmi.get("connectors"):
        return build_graph_from_xmi_model(
            model,
            user=user,
            name=name or _norm_text(model.get("name")) or "Generated",
        )

    return _new_graph(user, name or "Generated")


def upload_model(model: dict[str, Any], user: str = "", name: str = "") -> dict[str, Any]:
    fp = ensure_model_exists(user=user, name=name)
    current = _load_model(fp)

    if not isinstance(model, dict):
        raise ValueError("model must be a dict")

    incoming_format = (_norm_text(model.get("source_format")) or "").lower()
    current.update(model)

    current.setdefault("elements", [])
    current.setdefault("connectors", [])
    current.setdefault(
        "xmi",
        {
            "elements": current.get("elements", []),
            "connectors": current.get("connectors", []),
        },
    )
    current.setdefault("ttl", {})
    current.setdefault("ttl_raw", "")

    if incoming_format == "xmi":
        current["source_format"] = "xmi"
        current["ttl_raw"] = ""
    elif incoming_format == "ttl":
        current["source_format"] = "ttl"
    else:
        current["source_format"] = _default_source_format(current)

    if not isinstance(current.get("xmi"), dict):
        current["xmi"] = {
            "elements": current.get("elements", []),
            "connectors": current.get("connectors", []),
        }

    if current.get("xmi", {}).get("elements") or current.get("xmi", {}).get("connectors"):
        current["elements"] = current["xmi"].get("elements", [])
        current["connectors"] = current["xmi"].get("connectors", [])
    else:
        current["xmi"] = {
            "elements": current.get("elements", []),
            "connectors": current.get("connectors", []),
        }

    connector_overrides = _extract_connector_overrides(current)

    if current["source_format"] == "ttl" and _norm_text(current.get("ttl_raw")):
        g = Graph()
        g.parse(data=current["ttl_raw"], format="turtle")
        current = _sync_model_from_graph(
            g,
            current,
            package_name=name or "Generated",
            source_format=current["source_format"],
            connector_overrides=connector_overrides,
        )
    elif current.get("xmi", {}).get("elements") or current.get("xmi", {}).get("connectors"):
        g = build_graph_from_xmi_model(
            current,
            user=user,
            name=name or "Generated",
        )
        current = _sync_model_from_graph(
            g,
            current,
            package_name=name or "Generated",
            source_format=current["source_format"],
            connector_overrides=connector_overrides,
        )
    else:
        current["xmi"] = {
            "elements": current.get("elements", []),
            "connectors": current.get("connectors", []),
        }
        current["elements"] = current["xmi"].get("elements", [])
        current["connectors"] = current["xmi"].get("connectors", [])

    return _save_model(fp, current)


def load_full_model(user: str = "", name: str = "") -> dict[str, Any]:
    fp = ensure_model_exists(user=user, name=name)
    return _load_model(fp)


def get_model(user: str = "", name: str = "") -> dict[str, Any]:
    fp = ensure_model_exists(user=user, name=name)
    return _load_model(fp)


def _iter_referenceable_class_uris(g: Graph):
    seen: set[str] = set()

    def _yield_if_uri(node):
        if isinstance(node, URIRef):
            s = str(node)
            if s not in seen and primitive_from_range(node) is None:
                seen.add(s)
                yield node

    for cls in g.subjects(RDF.type, OWL.Class):
        yield from _yield_if_uri(cls)

    for _, _, domain in g.triples((None, RDFS.domain, None)):
        yield from _yield_if_uri(domain)

    for _, _, rng in g.triples((None, RDFS.range, None)):
        yield from _yield_if_uri(rng)

    for child, parent in g.subject_objects(RDFS.subClassOf):
        yield from _yield_if_uri(child)
        yield from _yield_if_uri(parent)


def _is_referenceable_class_uri(g: Graph, uri: URIRef) -> bool:
    if primitive_from_range(uri) is not None:
        return False

    if (uri, RDF.type, OWL.Class) in g:
        return True

    if (None, RDFS.domain, uri) in g:
        return True

    if (None, RDFS.range, uri) in g:
        return True

    if (uri, RDFS.subClassOf, None) in g:
        return True

    if (None, RDFS.subClassOf, uri) in g:
        return True

    return False


def find_class_by_label(g: Graph, class_name: str) -> URIRef | None:
    wanted = _canonical_text(class_name)
    if not wanted:
        return None

    if "://" in class_name or class_name.startswith("urn:"):
        uri = URIRef(class_name)
        if _is_referenceable_class_uri(g, uri):
            return uri

    for s in _iter_referenceable_class_uris(g):
        if _canonical_text(local_name(s)) == wanted:
            return s

        label = get_label(g, s)
        if label and _canonical_text(label) == wanted:
            return s

        for raw_label in g.objects(s, RDFS.label):
            if _canonical_text(str(raw_label)) == wanted:
                return s

    return None


def find_class_by_label_or_xmi(g: Graph, class_name: str, package_name: str | None = None) -> URIRef | None:
    found = find_class_by_label(g, class_name)
    if found:
        return found

    wanted = _canonical_text(class_name)
    if not wanted:
        return None

    xmi = build_model(g, package_name=package_name)
    for el in xmi.get("elements", []):
        if el.get("type") != "uml:Class":
            continue
        if _canonical_text(el.get("name")) != wanted:
            continue

        for tag in el.get("tags", []):
            if tag.get("name") == "uri" and tag.get("value"):
                return URIRef(tag["value"])

    return None


def _find_class_result(model: dict[str, Any], title: str | None = None, element_id: str | None = None) -> dict[str, Any]:
    for el in model.get("xmi", {}).get("elements", []):
        if el.get("type") != "uml:Class":
            continue
        if element_id and el.get("ID") == element_id:
            return el
        if title and _canonical_text(el.get("name")) == _canonical_text(title):
            return el
    return {}


def _find_attribute_result(model: dict[str, Any], class_name: str, attr_uri: str) -> dict[str, Any]:
    for el in model.get("xmi", {}).get("elements", []):
        if el.get("type") == "uml:Class" and _canonical_text(el.get("name")) == _canonical_text(class_name):
            for attr in el.get("attributes", []):
                for tag in attr.get("tags_attribute", []):
                    if tag.get("name") == "uri" and tag.get("value") == attr_uri:
                        return attr
    return {}


def _find_connector_result(
    model: dict[str, Any],
    rel_uri: str = "",
    relationship: str = "",
    source_name: str = "",
    target_name: str = "",
    rel_label: str = "",
    connector_id: str = "",
) -> dict[str, Any]:
    for conn in model.get("xmi", {}).get("connectors", []):
        if connector_id and _connector_id_from_view(conn) == connector_id:
            return conn

        semantic_uri = _semantic_uri_from_connector_view(conn)
        if relationship == "Generalization":
            semantic_match = True
        else:
            semantic_match = (not rel_uri) or semantic_uri == rel_uri

        if (
            semantic_match
            and ((not relationship) or _safe_xmi_value(conn.get("relationship")) == relationship)
            and ((not source_name) or _canonical_text(conn.get("source_name")) == _canonical_text(source_name))
            and ((not target_name) or _canonical_text(conn.get("target_name")) == _canonical_text(target_name))
            and ((not rel_label) or _canonical_text(conn.get("name")) == _canonical_text(rel_label))
        ):
            return conn

    return {}


def add_class(
    title: str,
    definition: str,
    usage_note: str,
    user: str = "",
    name: str = "",
    package: str | None = None,
    ID: str | None = None,
) -> dict[str, Any]:
    package = package or ""
    ID = ID or ""

    fp = ensure_model_exists(user=user, name=name)
    model = _load_model(fp)
    g = _graph_from_model(model, user=user, name=name or "Generated")

    existing = find_class_by_label(g, title)
    if existing:
        model = _sync_model_from_graph(
            g,
            model,
            package_name=name or "Generated",
            source_format=_default_source_format(model),
        )
        _save_model(fp, model)
        return _find_class_result(model, title=title, element_id=ID or None) or {
            "error": f"La classe '{title}' existe déjà."
        }

    class_uri = _coerce_class_uri(ID, title)
    g.add((class_uri, RDF.type, OWL.Class))
    _set_literal(g, class_uri, RDFS.label, title, lang="fr")
    _set_literal(g, class_uri, RDFS.comment, definition, lang="fr")
    _set_literal(g, class_uri, SKOS.scopeNote, usage_note, lang="fr")
    if ID:
        _set_literal(g, class_uri, UML_META.eaid, ID)

    model = _sync_model_from_graph(
        g,
        model,
        package_name=name or "Generated",
        source_format=_default_source_format(model),
    )
    model = _save_model(fp, model)

    return _find_class_result(model, title=title, element_id=ID or None) or {
        "error": f"Classe '{title}' créée mais introuvable dans le résultat."
    }


def add_attribute(
    class_name: str,
    attr_label: str,
    attr_definition: str,
    attr_uri: str,
    attr_usage_note: str = "",
    attr_type: str | None = "",
    user: str = "",
    name: str = "",
) -> dict[str, Any]:
    fp = ensure_model_exists(user=user, name=name)
    model = _load_model(fp)
    g = _graph_from_model(model, user=user, name=name or "Generated")

    class_uri = find_class_by_label(g, class_name)
    if not class_uri:
        return {"error": f"Classe source introuvable : '{class_name}'"}

    prop_uri = URIRef(attr_uri)
    _set_literal(g, prop_uri, RDFS.label, attr_label, lang="fr")
    _set_literal(g, prop_uri, RDFS.comment, attr_definition, lang="fr")
    _set_literal(g, prop_uri, SKOS.scopeNote, attr_usage_note, lang="fr")
    _add_uri(g, prop_uri, RDFS.domain, class_uri)

    canonical_range_uri, is_primitive = resolve_type_uri(attr_type)

    g.remove((prop_uri, RDF.type, OWL.DatatypeProperty))
    g.remove((prop_uri, RDF.type, OWL.ObjectProperty))

    if is_primitive:
        g.add((prop_uri, RDF.type, OWL.DatatypeProperty))
        if canonical_range_uri:
            _set_uri(g, prop_uri, RDFS.range, canonical_range_uri)
    else:
        g.add((prop_uri, RDF.type, OWL.ObjectProperty))
        if canonical_range_uri:
            _add_uri(g, prop_uri, RDFS.range, canonical_range_uri)

    model = _sync_model_from_graph(
        g,
        model,
        package_name=name or "Generated",
        source_format=_default_source_format(model),
    )
    model = _save_model(fp, model)

    return _find_attribute_result(model, class_name=class_name, attr_uri=attr_uri) or {
        "error": f"Attribut '{attr_label}' créé mais introuvable dans le résultat."
    }


def add_connector(
    source_name: str,
    target_name: str,
    rel_label: str,
    rel_definition: str,
    rel_uri: str,
    relationship: str,
    lb: str = "",
    rb: str = "",
    lt: str = "",
    rt: str = "",
    rel_usage_note: str = "",
    user: str = "",
    name: str = "",
) -> dict[str, Any]:
    fp = ensure_model_exists(user=user, name=name)
    model = _load_model(fp)
    g = _graph_from_model(model, user=user, name=name or "Generated")

    source_uri = find_class_by_label_or_xmi(g, source_name, package_name=name or "Generated")
    if not source_uri:
        return {"error": f"Classe source introuvable : '{source_name}'"}

    target_uri = find_class_by_label_or_xmi(g, target_name, package_name=name or "Generated")
    if not target_uri:
        return {"error": f"Classe cible introuvable : '{target_name}'"}

    relationship = _norm_text(relationship) or "Association"
    lb = _safe_xmi_value(lb)
    rb = _safe_xmi_value(rb)
    lt = _safe_xmi_value(lt)
    rt = _safe_xmi_value(rt)
    rel_uri = _safe_xmi_value(rel_uri)

    connector_semantic_uri = "" if relationship == "Generalization" else rel_uri
    connector_id = _build_connector_id(
        connector_semantic_uri,
        relationship,
        str(source_uri),
        str(target_uri),
    )

    if relationship == "Generalization":
        g.add((source_uri, RDFS.subClassOf, target_uri))
    else:
        prop_uri = URIRef(rel_uri)
        g.remove((prop_uri, RDF.type, OWL.DatatypeProperty))
        g.add((prop_uri, RDF.type, OWL.ObjectProperty))

        _set_literal(g, prop_uri, RDFS.label, rel_label, lang="fr")
        _set_literal(g, prop_uri, RDFS.comment, rel_definition, lang="fr")
        _set_literal(g, prop_uri, SKOS.scopeNote, rel_usage_note, lang="fr")
        _add_uri(g, prop_uri, RDFS.domain, source_uri)
        _add_uri(g, prop_uri, RDFS.range, target_uri)
        _set_literal(g, prop_uri, UML_META.relationshipType, relationship)

    connector_overrides = _extract_connector_overrides(model)
    override_key = _connector_key_from_parts(
        connector_semantic_uri,
        relationship,
        str(source_uri),
        str(target_uri),
    )
    connector_overrides[override_key] = {
        "connector_id": connector_id,
        "semantic_uri": connector_semantic_uri,
        "relationship": relationship,
        "name": rel_label or ("subClassOf" if relationship == "Generalization" else local_name(rel_uri)),
        "lb": lb,
        "lt": lt,
        "rb": rb,
        "rt": rt,
        "source_name": source_name,
        "target_name": target_name,
    }

    model = _sync_model_from_graph(
        g,
        model,
        package_name=name or "Generated",
        source_format=_default_source_format(model),
        connector_overrides=connector_overrides,
    )
    model = _save_model(fp, model)

    return _find_connector_result(
        model,
        rel_uri=rel_uri,
        relationship=relationship,
        source_name=source_name,
        target_name=target_name,
        rel_label=rel_label,
        connector_id=connector_id,
    ) or {
        "error": f"Connecteur '{rel_label}' créé mais introuvable dans le résultat."
    }


def _remove_none_deep(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _remove_none_deep(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_remove_none_deep(v) for v in value if v is not None]
    return value


def build_xmi_bytes(model: dict[str, Any]) -> bytes:
    xmi = _remove_none_deep(model.get("xmi", {}))
    elements = xmi.get("elements", [])
    connectors = xmi.get("connectors", [])

    NS_XMI = "http://www.omg.org/spec/XMI/20131001"
    NS_UML = "http://www.omg.org/spec/UML/20161101"

    root = Element(
        "xmi:XMI",
        {
            "xmlns:xmi": NS_XMI,
            "xmlns:uml": NS_UML,
            "xmi:version": "2.5.1",
        },
    )

    model_id = ea_id("MODEL", model.get("name") or "model")
    uml_model = SubElement(
        root,
        "uml:Model",
        {
            "xmi:type": "uml:Model",
            "xmi:id": model_id,
            "name": _safe_xmi_value(model.get("name") or "model"),
        },
    )

    root_package = None
    class_elements: list[dict[str, Any]] = []

    for el in elements:
        if el.get("type") == "uml:Package" and root_package is None:
            root_package = el
        elif el.get("type") == "uml:Class":
            class_elements.append(el)

    if root_package is None:
        root_package = {
            "ID": ea_id("EAPK", model.get("name") or "package"),
            "name": _safe_xmi_value(model.get("name") or "Package"),
            "type": "uml:Package",
            "package": "",
            "tags": [],
        }

    package_el = SubElement(
        uml_model,
        "packagedElement",
        {
            "xmi:type": "uml:Package",
            "xmi:id": _safe_xmi_value(root_package.get("ID")),
            "name": _safe_xmi_value(root_package.get("name")),
        },
    )

    class_id_to_xml: dict[str, Element] = {}

    def _parse_type_to_href(attr_type: str) -> str | None:
        primitive_map = {
            "String": "String",
            "Boolean": "Boolean",
            "Integer": "Integer",
            "Real": "Real",
            "Date": "Date",
            "DateTime": "DateTime",
            "Time": "Time",
            "URI": "UnlimitedNatural",
        }
        t = _safe_xmi_value(attr_type)
        if not t:
            return None
        return primitive_map.get(t)

    for cls in class_elements:
        cls_id = _safe_xmi_value(cls.get("ID"))
        cls_name = _safe_xmi_value(cls.get("name"))

        cls_el = SubElement(
            package_el,
            "packagedElement",
            {
                "xmi:type": "uml:Class",
                "xmi:id": cls_id,
                "name": cls_name,
                "visibility": "public",
            },
        )
        class_id_to_xml[cls_id] = cls_el

        for attr in cls.get("attributes", []):
            attr_name = _safe_xmi_value(attr.get("name"))
            attr_type = _safe_xmi_value(attr.get("type"))
            attr_id = ea_id("ATTR", f"{cls_id}:{attr_name}")

            attr_attribs = {
                "xmi:type": "uml:Property",
                "xmi:id": attr_id,
                "name": attr_name,
                "visibility": "public",
            }

            href_type = _parse_type_to_href(attr_type)
            if href_type:
                attr_attribs["type"] = href_type

            owned_attr = SubElement(cls_el, "ownedAttribute", attr_attribs)

            lower = _safe_xmi_value(attr.get("lower_bounds"))
            upper = _safe_xmi_value(attr.get("upper_bounds"))

            if lower:
                SubElement(
                    owned_attr,
                    "lowerValue",
                    {
                        "xmi:type": "uml:LiteralInteger",
                        "xmi:id": ea_id("LOW", f"{attr_id}:lower"),
                        "value": lower,
                    },
                )

            if upper:
                SubElement(
                    owned_attr,
                    "upperValue",
                    {
                        "xmi:type": "uml:LiteralUnlimitedNatural",
                        "xmi:id": ea_id("UP", f"{attr_id}:upper"),
                        "value": upper,
                    },
                )

    def _add_multiplicity(end_el: Element, raw: str, seed: str) -> None:
        raw = _safe_xmi_value(raw)
        if not raw:
            return

        if ".." in raw:
            lower, upper = raw.split("..", 1)
        else:
            lower, upper = raw, raw

        lower = lower.strip()
        upper = upper.strip()

        if lower:
            SubElement(
                end_el,
                "lowerValue",
                {
                    "xmi:type": "uml:LiteralInteger",
                    "xmi:id": ea_id("LOW", f"{seed}:lower"),
                    "value": lower if lower != "*" else "0",
                },
            )

        if upper:
            SubElement(
                end_el,
                "upperValue",
                {
                    "xmi:type": "uml:LiteralUnlimitedNatural",
                    "xmi:id": ea_id("UP", f"{seed}:upper"),
                    "value": upper,
                },
            )

    for conn in connectors:
        relationship = _safe_xmi_value(conn.get("relationship"))
        source_id = _safe_xmi_value(conn.get("source_id"))
        target_id = _safe_xmi_value(conn.get("target_id"))
        rel_name = _safe_xmi_value(conn.get("name"))
        connector_id = _connector_id_from_view(conn) or ea_id(
            "CONN",
            f"{source_id}:{target_id}:{rel_name}:{relationship}",
        )

        if not source_id or not target_id:
            continue

        if relationship == "Generalization":
            source_cls = class_id_to_xml.get(source_id)
            if source_cls is None:
                continue

            SubElement(
                source_cls,
                "generalization",
                {
                    "xmi:type": "uml:Generalization",
                    "xmi:id": connector_id,
                    "general": target_id,
                },
            )
            continue

        assoc_id = connector_id
        end1_id = ea_id("END", f"{assoc_id}:source")
        end2_id = ea_id("END", f"{assoc_id}:target")

        assoc_el = SubElement(
            package_el,
            "packagedElement",
            {
                "xmi:type": "uml:Association",
                "xmi:id": assoc_id,
                "name": rel_name,
                "visibility": "public",
                "memberEnd": f"{end1_id} {end2_id}",
            },
        )

        end1_attrs = {
            "xmi:type": "uml:Property",
            "xmi:id": end1_id,
            "name": _safe_xmi_value(conn.get("lt")) or "",
            "type": source_id,
            "association": assoc_id,
        }
        end2_attrs = {
            "xmi:type": "uml:Property",
            "xmi:id": end2_id,
            "name": _safe_xmi_value(conn.get("rt")) or "",
            "type": target_id,
            "association": assoc_id,
        }

        if relationship == "Aggregation":
            end2_attrs["aggregation"] = "shared"
        elif relationship == "Composition":
            end2_attrs["aggregation"] = "composite"

        end1 = SubElement(assoc_el, "ownedEnd", end1_attrs)
        end2 = SubElement(assoc_el, "ownedEnd", end2_attrs)

        _add_multiplicity(end1, _safe_xmi_value(conn.get("lb")), end1_id)
        _add_multiplicity(end2, _safe_xmi_value(conn.get("rb")), end2_id)

    return tostring(root, encoding="utf-8", xml_declaration=True)


def get_export_info(user: str = "", name: str = "", target_format: str = "") -> dict[str, str]:
    model = get_model(user=user, name=name)
    export_format = (_norm_text(target_format) or _default_source_format(model)).lower()
    model_name = _sanitize_path_component(name, "model")

    if export_format == "xmi":
        return {
            "source_format": export_format,
            "filename": f"{model_name}.xmi",
            "mimetype": "application/xml",
        }

    return {
        "source_format": export_format,
        "filename": f"{model_name}.ttl",
        "mimetype": "text/turtle",
    }


def build_ttl_bytes(model: dict[str, Any], user: str = "", name: str = "") -> bytes:
    ttl_text = _norm_text(model.get("ttl_raw"))
    if ttl_text:
        return ttl_text.encode("utf-8")

    ttl_json = model.get("ttl")
    if ttl_json:
        g = Graph()
        g.parse(data=json.dumps(ttl_json, ensure_ascii=False), format="json-ld")
        ttl_raw = g.serialize(format="turtle")
        return ttl_raw.encode("utf-8") if isinstance(ttl_raw, str) else bytes(ttl_raw)

    g = build_graph_from_xmi_model(
        model,
        user=user,
        name=name or _norm_text(model.get("name")) or "Generated",
    )
    ttl_raw = g.serialize(format="turtle")
    return ttl_raw.encode("utf-8") if isinstance(ttl_raw, str) else bytes(ttl_raw)


def export_model_bytes(user: str = "", name: str = "", target_format: str = "") -> bytes:
    model = get_model(user=user, name=name)
    export_format = (_norm_text(target_format) or _default_source_format(model)).lower()

    if export_format == "xmi":
        return build_xmi_bytes(model)

    if export_format == "ttl":
        return build_ttl_bytes(model, user=user, name=name)

    raise ValueError(f"Unsupported export format: {export_format}")
