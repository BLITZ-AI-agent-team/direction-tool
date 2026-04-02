"""Direction - Embedding Client (Gemini API - new google-genai SDK)"""

import os
from dotenv import load_dotenv

load_dotenv()


def get_embedding(text):
    """Gemini text-embedding-004でテキストをベクトル化（768次元）"""
    from google import genai

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    result = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
    )
    return list(result.embeddings[0].values)


def get_embeddings_batch(texts, batch_size=100):
    """バッチでベクトル化"""
    from google import genai

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for text in batch:
            result = client.models.embed_content(
                model="gemini-embedding-001",
                contents=text,
            )
            all_embeddings.append(list(result.embeddings[0].values))

    return all_embeddings
