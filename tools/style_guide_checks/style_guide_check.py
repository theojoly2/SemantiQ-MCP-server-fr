from __future__ import annotations
import json
from fastmcp import Context
from .conventions import metadata_conventions, reuse_conventions


async def style_guide_check(
    ctx: Context = None,
    validator_check: dict = None,
    metadata_checks: dict = None,
    reuse_checks: dict = None,
    language: str = "fr",
) -> dict:

    """Generates a structured semantic interoperability assessment report for a data model using LLM summarization."""
    print("Validator Check:")
    print(json.dumps(validator_check, indent=2, ensure_ascii=False))

    if validator_check is None:
        validator_check = {}
    if metadata_checks is None:
        metadata_checks = {}
    if reuse_checks is None:
        reuse_checks = {}

    language = (language or "fr").strip().lower() or "fr"
    lang_instruction = {
        "fr": "Rédige TOUJOURS en français. Les titres, les explications, les exemples et les recommandations doivent être en français.",
        "en": "Write ALWAYS in English.",
    }.get(language, f"Rédige TOUJOURS en {language}.")

    # Fixed report structure (French by default, English handled via instruction)
    report_template = (
        "# Rapport d'évaluation du guide de style sémantique\n\n"
        "{{validation_section}}\n\n"
        "{{metadata_section}}\n\n"
        "{{reuse_section}}\n"
    )

    # Compose the validator section prompt
    validator_prompt = (
        "Tu es un expert en modélisation de données sémantiques. À partir des vérifications suivantes d'un modèle de données "
        "par rapport au validateur du guide de style SEMIC, rédige un résumé pour la section 'Validation par rapport au validateur ITB' "
        "comme suit : pour chaque règle non respectée, crée une sous-section avec le nom ou l'identifiant de la règle comme titre. "
        "Dans chaque sous-section, indique : (1) un résumé expliquant l'erreur constatée, (2) si disponible, un exemple du modèle de données "
        "illustrant l'erreur, (3) une suggestion claire pour corriger l'erreur, et (4) une liste des concepts ou éléments problématiques. "
        "Utilise uniquement les informations présentes dans validator_checks. Structure le résultat en markdown. "
        f"{lang_instruction}\n\n"
        f"Données de validation fournies :\n{json.dumps(validator_check, indent=2, ensure_ascii=False)}"
    )

    # Compose the metadata section prompt
    metadata_prompt = (
        f"Tu es un expert en modélisation de données sémantiques. À partir des vérifications suivantes d'un modèle de données "
        f"par rapport aux conventions du guide de style SEMIC : {metadata_conventions} \n"
        "rédige un résumé pour la section 'Qualité des métadonnées' comme suit : pour chaque convention non respectée, "
        "crée une sous-section avec le nom de la convention comme titre. Dans chaque sous-section, indique : (1) un résumé "
        "expliquant l'erreur constatée, (2) si disponible, un exemple du modèle de données illustrant l'erreur, (3) une suggestion "
        "claire pour corriger l'erreur, et (4) une liste des concepts ou éléments problématiques. Utilise uniquement les informations "
        f"présentes dans metadata_checks. Structure le résultat en markdown. {lang_instruction}\n\n"
        f"Données de vérification des métadonnées fournies :\n{json.dumps(metadata_checks, indent=2, ensure_ascii=False)}"
    )

    # Compose the reuse section prompt
    reuse_prompt = (
        f"Tu es un expert en interopérabilité sémantique. À partir des vérifications de réutilisation suivantes pour chaque classe "
        f"par rapport aux conventions du guide de style SEMIC : {reuse_conventions} \n"
        "rédige un résumé pour la section 'Réutilisation des standards' comme suit : pour chaque convention non respectée, "
        "crée une sous-section avec le nom de la convention comme titre. Dans chaque sous-section, indique : (1) un résumé "
        "expliquant l'erreur constatée, (2) si disponible, un exemple du modèle de données illustrant l'erreur, (3) une suggestion "
        "claire pour corriger l'erreur, et (4) une liste des concepts ou éléments problématiques. Utilise uniquement les informations "
        f"présentes dans reuse_checks. Structure le résultat en markdown. {lang_instruction}\n\n"
        f"Données de vérification de réutilisation fournies :\n{json.dumps(reuse_checks, indent=2, ensure_ascii=False)}"
    )

    system_prompt = (
        "Tu es un expert en modélisation de données sémantiques rédigeant un rapport d'évaluation fondé sur des standards. "
        f"{lang_instruction}"
    )

    # Get LLM summaries for each section
    if validator_check:
        validator_response = await ctx.sample(
            messages=[validator_prompt],
            system_prompt=system_prompt,
            temperature=0.0,
            max_tokens=3000,
        )
    else:
        validator_response = ""

    if metadata_checks:
        metadata_response = await ctx.sample(
            messages=[metadata_prompt],
            system_prompt=system_prompt,
            temperature=0.0,
            max_tokens=3000,
        )
    else:
        metadata_response = ""

    if reuse_checks:
        reuse_response = await ctx.sample(
            messages=[reuse_prompt],
            system_prompt=system_prompt,
            temperature=0.0,
            max_tokens=3000,
        )
    else:
        reuse_response = ""

    # Extract text from LLM responses
    validation_section = getattr(validator_response, "text", str(validator_response)).strip()
    metadata_section = getattr(metadata_response, "text", str(metadata_response)).strip()
    reuse_section = getattr(reuse_response, "text", str(reuse_response)).strip()

    # Fill the template
    report = report_template.replace("{{validation_section}}", validation_section).replace("{{metadata_section}}", metadata_section).replace("{{reuse_section}}", reuse_section)

    return {"report": report}


style_guide_check.__doc__ = f"""
    This tool produces a markdown report with a fixed structure, summarizing the results of an assessment of data model against the SEMIC style guide convention.
    Args:
        ctx (Context, optional):
            The LLM context for prompt completion.

    Returns:
        dict:
            A dictionary with a single key 'report' containing the full markdown assessment report as a string.

    Notes:
        - The report is intended to provide a standards-based, human-readable assessment of a data model's semantic interoperability, metadata quality, and reuse of standards.
        - All sections are generated using LLM summarization for clarity and actionable recommendations.
"""
