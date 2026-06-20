try:
    from .load_documents import config as cf
except ImportError:
    from load_documents import config as cf

client = cf.client
COLLECTION = cf.COLLECTION


def get_document_file(document_id: str) -> dict:
    """
    Récupère le fichier Base64 stocké dans Qdrant à partir de l'ID du point.
    """
    try:
        # 1. L'interface envoie l'ID en texte ("6198741576312262109"), 
        # mais Qdrant a besoin d'un entier (int) pour retrouver le point physique.
        point_id = int(document_id)

        # 2. On utilise 'retrieve' pour chercher instantanément le point par son ID Qdrant
        records = client.retrieve(
            collection_name=COLLECTION,
            ids=[point_id],
            with_payload=True
        )

        if not records:
            return {"success": False, "error": f"Aucun document trouvé pour l'ID Qdrant: {document_id}"}

        payload = records[0].payload
        file_base64 = payload.get("file_base64")

        if not file_base64:
            return {"success": False, "error": "Le champ file_base64 est absent. Assurez-vous que le document a bien été indexé avec le fichier encodé."}

        # 3. On renvoie les données à l'interface
        return {
            "success": True,
            "file_base64": file_base64,
            "filename": payload.get("filename", "document_original"),
            "extension": payload.get("source_extension", ".txt")
        }

    except ValueError:
        return {"success": False, "error": "L'ID fourni n'est pas un nombre entier valide."}
    except Exception as e:
        return {"success": False, "error": f"Erreur technique Qdrant : {str(e)}"}
