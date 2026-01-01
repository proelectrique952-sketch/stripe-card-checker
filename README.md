# stripe-card-checker

# Test endpoint
curl http://localhost:5000/api/stripe/auth/test

# Generate test user
curl http://localhost:5000/api/stripe/auth/generate-user

# Check card (POST request)
curl -X POST http://localhost:5000/api/stripe/auth/check \
  -H "Content-Type: application/json" \
  -d '{
    "card_number": "4242424242424242",
    "exp_month": "12",
    "exp_year": "2025",
    "cvv": "123"
  }'
