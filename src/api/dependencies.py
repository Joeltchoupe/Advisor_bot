# api/dependencies.py

from fastapi import Header, HTTPException
from services.database import get_client

def verify_api_key(x_api_key: str = Header(...)) -> str:
    """
    Auth V1 : une API key par company.
    Retourne company_id si ok.
    """
    client = get_client()
    result = (
        client.table("companies")
        .select("id")
        .eq("api_key", x_api_key)
        .limit(1)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=401, detail="Non autorisé")

    return result.data[0]["id"]
