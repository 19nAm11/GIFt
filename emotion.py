"""Look at a photo and name the emotion in it."""
import sys
from google import genai
from google.genai import types

MODEL = "gemini-3.1-flash-lite"

PROMPT = (
    "Look at the person's facial expression. "
    "Reply with a short GIF search phrase describing the emotion, "
    "like 'a person laughing happily' or 'someone looking shocked'. "
    "Reply with the phrase only."
)


def describe_emotion(image_bytes, mime_type="image/jpeg"):
    client = genai.Client()  # uses your GEMINI_API_KEY
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            PROMPT,
        ],
    )
    return response.text.strip()


if __name__ == "__main__":
    with open(sys.argv[1], "rb") as f:
        print(describe_emotion(f.read()))