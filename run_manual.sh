#!/bin/bash
curl -X POST "https://ai-accounting-agent-jfzhrxdx4a-ez.a.run.app/solve" \
  -H "Content-Type: application/json" \
  -H "x-api-key: AIzaSyArjkFLSEYddFtlTBi7hBACylmO_SOUtrM" \
  -d '{
    "prompt": "Lag en kunde som heter Norsk Teknologifabrikk. Selskapet har organisasjonsnummer 998877665 MVA, epost post@norskteknologi.no, og en adresse på Karl Johans Gate 1, 0154 Oslo.",
    "files": [],
    "tripletex_credentials": {
      "base_url": "https://kkpqfuj-amager.tripletex.dev/v2",
      "session_token": "eyJ0b2tlbklkIjoyMTQ3NjI4NTMwLCJ0b2tlbiI6ImYyODcwYTExLTNiYmQtNDI1Mi04YzRmLWM4YmYwNmRkYWY0NiJ9"
    }
  }'
