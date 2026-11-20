from dotenv import load_dotenv
from openai import OpenAI
import os

load_dotenv()

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

response = client.responses.create(
    model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    input="Say hello to my Patterno Watchtower project."
)

print(response.output_text)
print("Tokens used:", response.usage.total_tokens)