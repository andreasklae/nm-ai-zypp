import asyncio
from ai_accounting_agent.v2.agent import execute_agent_v2
from ai_accounting_agent.schemas import SolveRequest, TripletexCredentials
import os
from dotenv import load_dotenv

load_dotenv("src/ai_accounting_agent/.env")
TOKEN = os.getenv("TRIPLETEX_SESSION_TOKEN")

async def test():
    req = SolveRequest(
        prompt="Lag en kunde som heter Norsk Teknologifabrikk. Selskapet har organisasjonsnummer 998877665 MVA, epost post@norskteknologi.no, og en adresse på Karl Johans Gate 1, 0154 Oslo.",
        tripletex_credentials=TripletexCredentials(
            base_url="https://kkpqfuj-amager.tripletex.dev/v2",
            session_token=TOKEN
        )
    )
    result = await execute_agent_v2(request=req, attachments=[], run_id="v2_test_local")
    print(result.output)

asyncio.run(test())
