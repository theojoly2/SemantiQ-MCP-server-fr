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
    """Construit un identifiant stable de type EAID/EAPK à partir d'une URI."""
    u = uuid.uuid5(uuid.NAMESPACE_URL, str(uri))
    s = str(u).replace("-", "_").upper()
    return f"{prefix}_{s}"


def local_name(uri: URIRef | str) -> str:
    """Extrait le nom local d'une URI RDF."""
    s = str(uri)
    if "#" in s:
        return s.split("#")[-1]
    return s.rstrip("/").split("/")[-1]


def _norm_text(value: Any) -> str | None:
    """Normalise une valeur texte ; renvoie None si vide."""
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def _safe_xmi_value(value: Any) -> str:
    """Retourne une chaîne sûre pour l'export XMI/XML ; jamais None."""
    value = _norm_text(value)
    return value if value is not None else ""


def _canonical_text(value: Any) -> str:
    """Normalise fortement un texte pour les comparaisons souples."""
    value = _norm_text(value) or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return value


def _slugify_uri(value: str) -> str:
    """Construit un slug sûr pour une URI interne."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "generated"


def _sanitize_path_component(value: str | None, default: str) -> str:
    """Nettoie un segment de chemin sans changer sa casse métier."""
    value = _norm_text(value) or default
    value = value.replace("\\", "_").replace("/", "_").replace("\x00", "")
    return value.strip() or default


def _find_file(user: str, name: str) -> Path:
    """Retourne le chemin du fichier modèle JSON pour un utilisateur et un nom de modèle."""
    safe_user = _sanitize_path_component(user, "default")
    safe_name = _sanitize_path_component(name, "generated")
    return BASE_MODELS_PATH / safe_user / f"{safe_name}.json"


def get_model_path(user: str = "", name: str = "") -> str:
    """Retourne le chemin absolu du fichier modèle JSON."""
    return str(_find_file(user, name))


def _model_uri(user: str, name: str) -> URIRef:
    """Construit l'URI d'ontologie du modèle."""
    return URIRef(
        f"urn:ai4semantics:model:{_slugify_uri(user or 'default')}:{_slugify_uri(name or 'generated')}"
    )


def _new_graph(user: str, name: str) -> Graph:
    """Crée un graphe RDF minimal pour un modèle vide."""
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
    """Retourne la meilleure valeur littérale pour un prédicat RDF donné."""
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
    """Retourne le label préféré d'une ressource."""
    return _first_literal(g, s, RDFS.label)


def get_comment(g: Graph, s: URIRef) -> str | None:
    """Retourne le commentaire préféré d'une ressource."""
    return _first_literal(g, s, RDFS.comment)


def get_scope_note(g: Graph, s: URIRef) -> str | None:
    """Retourne la scope note / note d'usage préférée d'une ressource."""
    return _first_literal(g, s, SKOS.scopeNote)


def get_literal_value(g: Graph, s: URIRef, p: URIRef) -> str | None:
    """Retourne la première valeur littérale d'un prédicat, si elle existe."""
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
    """Remplace un littéral RDF en supprimant les anciennes valeurs."""
    g.remove((s, p, None))
    value = _norm_text(value)
    if value is not None:
        if lang:
            g.add((s, p, Literal(value, lang=lang)))
        else:
            g.add((s, p, Literal(value)))


def _set_uri(g: Graph, s: URIRef, p: URIRef, value: URIRef | str | None) -> None:
    """Remplace une URI RDF en supprimant les anciennes valeurs."""
    g.remove((s, p, None))
    if value:
        g.add((s, p, URIRef(str(value))))


def _coerce_class_uri(value: str | None, fallback_label: str) -> URIRef:
    """Construit une URI de classe à partir d'un ID EA ou d'un label."""
    value = _norm_text(value)
    if value and (value.startswith("http://") or value.startswith("https://") or value.startswith("urn:")):
        return URIRef(value)
    if value:
        return URIRef(f"urn:eaid:{value}")
    return URIRef(f"urn:class:{_slugify_uri(fallback_label)}")


def primitive_from_range(r: URIRef) -> str | None:
    """Mappe une URI RDF/XSD vers un nom de type simple pour la vue UML."""
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
    "date": str(XSD.date),
    "datetime": str(XSD.dateTime),
    "time": str(XSD.time),
    "uri": str(XSD.anyURI),
    "xsd:string": str(XSD.string),
    "xsd:boolean": str(XSD.boolean),
    "xsd:integer": str(XSD.integer),
    "xsd:date": str(XSD.date),
    "xsd:dateTime": str(XSD.dateTime),
    "xsd:anyURI": str(XSD.anyURI),
    "rdf:langString": str(RDF.langString),
    "rdfs:Literal": str(RDFS.Literal),
}


def resolve_type_uri(attr_type: str | None) -> tuple[str, bool]:
    """Résout un type d'attribut en URI canonique et indique si c'est un type primitif."""
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
    """Détermine le format source par défaut du modèle."""
    fmt = _norm_text(model.get("source_format"))
    if fmt:
        return fmt.lower()

    if model.get("ttl_raw"):
        return "ttl"
    return "xmi"


def extract_ontology_package(g: Graph, custom_name: str | None = None) -> dict[str, Any]:
    """Construit l'élément UML racine de type Package à partir de l'ontologie RDF."""
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


def build_model(g: Graph, package_name: str | None = None) -> dict[str, Any]:
    """Reconstruit la vue XMI-like à partir du graphe RDF."""
    model: dict[str, Any] = {"elements": [], "connectors": []}

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
        lb = _safe_xmi_value(get_literal_value(g, prop, UML_META.leftMultiplicity))
        lt = _safe_xmi_value(get_literal_value(g, prop, UML_META.leftRole))
        rb = _safe_xmi_value(get_literal_value(g, prop, UML_META.rightMultiplicity))
        rt = _safe_xmi_value(get_literal_value(g, prop, UML_META.rightRole))

        for domain in domains:
            src = ensure_class(domain)
            for rng in ranges:
                tgt = ensure_class(rng)
                tgt_tags = [{"name": "uri", "value": prop_uri}]
                if prop_comment:
                    tgt_tags.append({"name": "definition-en", "value": prop_comment})
                if prop_label:
                    tgt_tags.append({"name": "label-en", "value": prop_label})
                if prop_usage:
                    tgt_tags.append({"name": "usageNote-en", "value": prop_usage})

                model["connectors"].append(
                    {
                        "source_name": src["name"],
                        "source_id": src["ID"],
                        "target_name": tgt["name"],
                        "target_id": tgt["ID"],
                        "relationship": relationship,
                        "name": prop_label,
                        "lb": lb,
                        "lt": lt,
                        "rb": rb,
                        "rt": rt,
                        "tags": [],
                        "tags_source": [],
                        "tags_target": tgt_tags,
                    }
                )

    for child, parent in g.subject_objects(RDFS.subClassOf):
        if isinstance(parent, URIRef):
            child_elem = ensure_class(child)
            parent_elem = ensure_class(parent)
            model["connectors"].append(
                {
                    "source_name": child_elem["name"],
                    "source_id": child_elem["ID"],
                    "target_name": parent_elem["name"],
                    "target_id": parent_elem["ID"],
                    "relationship": "Generalization",
                    "name": "subClassOf",
                    "lb": "",
                    "lt": "",
                    "rb": "",
                    "rt": "",
                    "tags": [],
                    "tags_source": [],
                    "tags_target": [],
                }
            )

    return model


def _sync_model_from_graph(
    g: Graph,
    model: dict[str, Any],
    package_name: str | None = None,
    source_format: str | None = None,
) -> dict[str, Any]:
    """Resynchronise ttl, ttl_raw, xmi, elements et connectors à partir du graphe RDF."""
    json_ld_str = g.serialize(format="json-ld", indent=4)
    ttl_raw = g.serialize(format="turtle")
    xmi = build_model(g, package_name=package_name)

    model["ttl"] = json.loads(json_ld_str) if json_ld_str else {}
    model["ttl_raw"] = ttl_raw.decode("utf-8") if isinstance(ttl_raw, (bytes, bytearray)) else str(ttl_raw)

    model["xmi"] = xmi
    model["elements"] = xmi.get("elements", [])
    model["connectors"] = xmi.get("connectors", [])
    model["source_format"] = (source_format or model.get("source_format") or "ttl").lower()

    return model


def _save_model(fp: Path, model: dict[str, Any]) -> dict[str, Any]:
    """Écrit le modèle sur disque puis le relit."""
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_model(fp: Path) -> dict[str, Any]:
    """Charge un fichier modèle JSON ; renvoie {} s'il est absent, vide ou invalide."""
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
    """Garantit qu'un fichier modèle existe pour un utilisateur et un nom de modèle."""
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


def upload_model(model: dict[str, Any], user: str = "", name: str = "") -> dict[str, Any]:
    """
    Remplace ou fusionne un modèle JSON existant.

    Le champ `source_format` permet de préserver le format d'origine :
    - 'ttl' pour un modèle RDF/Turtle ;
    - 'xmi' pour un modèle UML/XMI.
    """
    fp = ensure_model_exists(user=user, name=name)
    current = _load_model(fp)

    if not isinstance(model, dict):
        raise ValueError("model must be a dict")

    current.update(model)

    current.setdefault("elements", [])
    current.setdefault("connectors", [])
    current.setdefault("xmi", {"elements": current["elements"], "connectors": current["connectors"]})
    current.setdefault("ttl", {})
    current.setdefault("ttl_raw", "")
    current["source_format"] = _default_source_format(current)

    if current.get("ttl_raw"):
        g = Graph()
        g.parse(data=current["ttl_raw"], format="turtle")
        current = _sync_model_from_graph(
            g,
            current,
            package_name=name or "Generated",
            source_format=current["source_format"],
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
    """Charge le modèle complet persistant."""
    fp = ensure_model_exists(user=user, name=name)
    return _load_model(fp)


def get_model(user: str = "", name: str = "") -> dict[str, Any]:
    """Alias de lecture du modèle courant."""
    fp = ensure_model_exists(user=user, name=name)
    return _load_model(fp)


def _iter_referenceable_class_uris(g: Graph):
    """
    Retourne les URI de classes utilisables dans les liens :
    - classes explicites owl:Class
    - classes référencées dans rdfs:domain / rdfs:range
    - classes impliquées dans rdfs:subClassOf
    Sans modifier leur statut RDF.
    """
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
    """
    Indique si une URI peut être utilisée comme classe cible/source
    même si elle n'est pas explicitement déclarée owl:Class.
    """
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
    """
    Recherche une classe par label, nom local ou URI.

    Important :
    - retrouve les classes explicites owl:Class ;
    - retrouve aussi les classes simplement référencées dans le graphe
      (domain/range/subClassOf) ;
    - ne crée rien et ne modifie pas leur statut RDF.
    """
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
    """
    Recherche une classe :
    1) dans le graphe RDF (classes explicites + classes référencées),
    2) sinon dans la vue XMI reconstruite, utile pour les classes externes
       matérialisées par ensure_class().
    """
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
    """Retrouve une classe dans la vue xmi après écriture."""
    for el in model.get("xmi", {}).get("elements", []):
        if el.get("type") != "uml:Class":
            continue
        if element_id and el.get("ID") == element_id:
            return el
        if title and _canonical_text(el.get("name")) == _canonical_text(title):
            return el
    return {}


def _find_attribute_result(model: dict[str, Any], class_name: str, attr_uri: str) -> dict[str, Any]:
    """Retrouve un attribut dans la vue xmi après écriture."""
    for el in model.get("xmi", {}).get("elements", []):
        if el.get("type") == "uml:Class" and _canonical_text(el.get("name")) == _canonical_text(class_name):
            for attr in el.get("attributes", []):
                for tag in attr.get("tags_attribute", []):
                    if tag.get("name") == "uri" and tag.get("value") == attr_uri:
                        return attr
    return {}


def _find_connector_result(model: dict[str, Any], rel_uri: str) -> dict[str, Any]:
    """Retrouve un connecteur dans la vue xmi après écriture."""
    for conn in model.get("xmi", {}).get("connectors", []):
        for tag in conn.get("tags_target", []):
            if tag.get("name") == "uri" and tag.get("value") == rel_uri:
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
    """Ajoute une classe au modèle."""
    package = package or ""
    ID = ID or ""

    fp = ensure_model_exists(user=user, name=name)
    model = _load_model(fp)

    g = Graph()
    if model.get("ttl_raw"):
        g.parse(data=model["ttl_raw"], format="turtle")
    else:
        g = _new_graph(user, name or "Generated")

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
    """Ajoute un attribut à une classe existante."""
    fp = ensure_model_exists(user=user, name=name)
    model = _load_model(fp)

    g = Graph()
    if model.get("ttl_raw"):
        g.parse(data=model["ttl_raw"], format="turtle")
    else:
        g = _new_graph(user, name or "Generated")

    class_uri = find_class_by_label(g, class_name)
    if not class_uri:
        return {"error": f"Classe source introuvable : '{class_name}'"}

    prop_uri = URIRef(attr_uri)
    _set_literal(g, prop_uri, RDFS.label, attr_label, lang="fr")
    _set_literal(g, prop_uri, RDFS.comment, attr_definition, lang="fr")
    _set_literal(g, prop_uri, SKOS.scopeNote, attr_usage_note, lang="fr")
    _set_uri(g, prop_uri, RDFS.domain, class_uri)

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
            _set_uri(g, prop_uri, RDFS.range, canonical_range_uri)

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
    """
    Ajoute une relation entre deux classes existantes du modèle.

    Ordre attendu des champs à remplir :
    1. source_name : nom de la classe source
    2. target_name : nom de la classe cible
    3. rel_label : nom lisible de la relation
    4. rel_definition : définition textuelle de la relation
    5. rel_uri : URI unique de la relation
    6. relationship : type UML ('Association', 'Composition', 'Aggregation', 'Generalization')
    7. lb : cardinalité côté source
    8. rb : cardinalité côté cible
    9. lt : rôle côté source
    10. rt : rôle côté cible
    11. rel_usage_note : note d'usage optionnelle
    12. user : utilisateur
    13. name : nom du modèle

    Règles strictes :
    - Toujours remplir lb et rb si l'utilisateur donne les cardinalités des deux côtés.
    - lb et rb sont des cardinalités uniquement : '1', '0..1', '*', '1..*'.
    - lt et rt sont des rôles uniquement : jamais des cardinalités.
    - Si aucun rôle n'est demandé, mettre lt='' et rt=''.
    """
    fp = ensure_model_exists(user=user, name=name)
    model = _load_model(fp)

    g = Graph()
    if model.get("ttl_raw"):
        g.parse(data=model["ttl_raw"], format="turtle")
    else:
        g = _new_graph(user, name or "Generated")

    source_uri = find_class_by_label_or_xmi(g, source_name, package_name=name or "Generated")
    if not source_uri:
        return {"error": f"Classe source introuvable : '{source_name}'"}

    target_uri = find_class_by_label_or_xmi(g, target_name, package_name=name or "Generated")
    if not target_uri:
        return {"error": f"Classe cible introuvable : '{target_name}'"}

    prop_uri = URIRef(rel_uri)
    relationship = _norm_text(relationship) or "Association"
    lb = _safe_xmi_value(lb)
    rb = _safe_xmi_value(rb)
    lt = _safe_xmi_value(lt)
    rt = _safe_xmi_value(rt)

    g.remove((prop_uri, RDF.type, OWL.DatatypeProperty))
    g.remove((prop_uri, RDF.type, OWL.ObjectProperty))
    g.add((prop_uri, RDF.type, OWL.ObjectProperty))

    _set_literal(g, prop_uri, RDFS.label, rel_label, lang="fr")
    _set_literal(g, prop_uri, RDFS.comment, rel_definition, lang="fr")
    _set_literal(g, prop_uri, SKOS.scopeNote, rel_usage_note, lang="fr")
    _set_uri(g, prop_uri, RDFS.domain, source_uri)
    _set_uri(g, prop_uri, RDFS.range, target_uri)

    _set_literal(g, prop_uri, UML_META.relationshipType, relationship)
    _set_literal(g, prop_uri, UML_META.leftMultiplicity, lb)
    _set_literal(g, prop_uri, UML_META.rightMultiplicity, rb)
    _set_literal(g, prop_uri, UML_META.leftRole, lt)
    _set_literal(g, prop_uri, UML_META.rightRole, rt)

    model = _sync_model_from_graph(
        g,
        model,
        package_name=name or "Generated",
        source_format=_default_source_format(model),
    )
    model = _save_model(fp, model)

    return _find_connector_result(model, rel_uri=rel_uri) or {
        "error": f"Connecteur '{rel_label}' créé mais introuvable dans le résultat."
    }


def _remove_none_deep(value: Any) -> Any:
    """Supprime récursivement les valeurs None d'une structure JSON."""
    if isinstance(value, dict):
        return {k: _remove_none_deep(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_remove_none_deep(v) for v in value if v is not None]
    return value


def build_xmi_bytes(model: dict[str, Any]) -> bytes:
    """
    Génère un XML simple de type XMI-like à partir de model['xmi'].

    Toute valeur None est supprimée ou convertie avant sérialisation pour éviter
    l'erreur 'cannot serialize None (type NoneType)'.
    """
    xmi = _remove_none_deep(model.get("xmi", {}))
    root = Element("xmi:XMI", {"xmlns:xmi": "http://www.omg.org/XMI"})

    package_el = SubElement(root, "uml:Model", {"xmlns:uml": "http://www.omg.org/spec/UML/20131001"})
    package_el.set("name", _safe_xmi_value(model.get("name") or "model"))

    elements = xmi.get("elements", [])
    connectors = xmi.get("connectors", [])

    for el in elements:
        attrs = {
            "id": _safe_xmi_value(el.get("ID")),
            "name": _safe_xmi_value(el.get("name")),
            "type": _safe_xmi_value(el.get("type")),
            "package": _safe_xmi_value(el.get("package")),
        }
        class_el = SubElement(package_el, "packagedElement", attrs)

        for tag in el.get("tags", []):
            tag_el = SubElement(class_el, "tag")
            tag_el.set("name", _safe_xmi_value(tag.get("name")))
            tag_el.set("value", _safe_xmi_value(tag.get("value")))

        for attr in el.get("attributes", []):
            attr_el = SubElement(class_el, "ownedAttribute")
            attr_el.set("name", _safe_xmi_value(attr.get("name")))
            attr_el.set("type", _safe_xmi_value(attr.get("type")))
            attr_el.set("lower", _safe_xmi_value(attr.get("lower_bounds")))
            attr_el.set("upper", _safe_xmi_value(attr.get("upper_bounds")))

            for tag in attr.get("tags_attribute", []):
                tag_el = SubElement(attr_el, "tag")
                tag_el.set("name", _safe_xmi_value(tag.get("name")))
                tag_el.set("value", _safe_xmi_value(tag.get("value")))

    for conn in connectors:
        conn_el = SubElement(package_el, "connector")
        conn_el.set("name", _safe_xmi_value(conn.get("name")))
        conn_el.set("relationship", _safe_xmi_value(conn.get("relationship")))
        conn_el.set("source_id", _safe_xmi_value(conn.get("source_id")))
        conn_el.set("target_id", _safe_xmi_value(conn.get("target_id")))
        conn_el.set("lb", _safe_xmi_value(conn.get("lb")))
        conn_el.set("rb", _safe_xmi_value(conn.get("rb")))
        conn_el.set("lt", _safe_xmi_value(conn.get("lt")))
        conn_el.set("rt", _safe_xmi_value(conn.get("rt")))

        for tag in conn.get("tags_target", []):
            tag_el = SubElement(conn_el, "tag")
            tag_el.set("name", _safe_xmi_value(tag.get("name")))
            tag_el.set("value", _safe_xmi_value(tag.get("value")))

    return tostring(root, encoding="utf-8", xml_declaration=True)


def get_export_info(user: str = "", name: str = "") -> dict[str, str]:
    """
    Retourne les métadonnées d'export à utiliser dans l'UI.

    Résultat :
    - source_format : ttl ou xmi
    - file_name
    - mime_type
    """
    model = get_model(user=user, name=name)
    source_format = _default_source_format(model)
    model_name = _sanitize_path_component(name, "model")

    if source_format == "xmi":
        return {
            "source_format": "xmi",
            "file_name": f"{model_name}.xmi",
            "mime_type": "application/xml",
        }

    return {
        "source_format": "ttl",
        "file_name": f"{model_name}.ttl",
        "mime_type": "text/turtle",
    }


def export_model_bytes(user: str = "", name: str = "") -> bytes:
    """
    Exporte le modèle dans son format d'origine.

    - Si le modèle vient d'un TTL : export .ttl
    - Si le modèle vient d'un XMI : export .xmi
    """
    model = get_model(user=user, name=name)
    source_format = _default_source_format(model)

    if source_format == "xmi":
        return build_xmi_bytes(model)

    ttl_text = model.get("ttl_raw", "") or ""
    return ttl_text.encode("utf-8")
