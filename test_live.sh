#!/bin/bash
TOKEN=$(python3 -c "import uuid; print(uuid.uuid4().hex[:10])")
echo "trace_token=$TOKEN"

curl -s -w "\nHTTP:%{http_code} TIME:%{time_total}s\n" \
  -X POST "https://ai-accounting-agent-jfzhrxdx4a-ez.a.run.app/solve" \
  -H "Content-Type: application/json" \
  -d "{
    \"prompt\": \"Lag en kunde som heter Norsk Teknologifabrikk. Selskapet har organisasjonsnummer 998877665 MVA, epost post@norskteknologi.no, og en adresse på Karl Johans Gate 1, 0154 Oslo. trace_token=$TOKEN\",
    \"files\": [],
    \"tripletex_credentials\": {
      \"base_url\": \"https://kkpqfuj-amager.tripletex.dev/v2\",
      \"session_token\": \"$(grep TRIPLETEX_SESSION_TOKEN src/ai_accounting_agent/.env | cut -d '=' -f2 | tr -d '\"')\"
    }
  }"

echo $TOKEN > last_token.txt
