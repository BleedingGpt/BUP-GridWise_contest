#!/bin/bash
# GridWise Optimizer - API Test Script

BASE_URL="http://localhost:8000"

echo "=== Testing /health ==="
curl -s "$BASE_URL/health" | python3 -m json.tool
echo -e "\n"

echo "=== Testing /optimize-energy (Sample 1) ==="
curl -s -X POST "$BASE_URL/optimize-energy" \
    -H "Content-Type: application/json" \
    -d @tests/test_sample_1.json | python3 -m json.tool
echo -e "\n"

echo "=== Testing /optimize-energy (Sample 2) ==="
curl -s -X POST "$BASE_URL/optimize-energy" \
    -H "Content-Type: application/json" \
    -d @tests/test_sample_2.json | python3 -m json.tool
echo -e "\n"

echo "=== Testing /docs (FastAPI auto-docs) ==="
echo "Open: $BASE_URL/docs"
echo -e "\n"

echo "=== Testing frontend ==="
echo "Open: $BASE_URL/"
