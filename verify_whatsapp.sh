# Verify WhatsApp Cloud API token (run after filling .env)
# Requires: curl, jq (optional for pretty print)
source .env 2>/dev/null || echo "Warning: .env not sourced automatically; set WHATSAPP_TOKEN and WHATSAPP_PHONE_ID manually."
if [[ -z "$WHATSAPP_TOKEN" || -z "$WHATSAPP_PHONE_ID" ]]; then
  echo "Error: WHATSAPP_TOKEN and WHATSAPP_PHONE_ID must be set in .env"
  exit 1
fi
echo "Testing WhatsApp Cloud API credentials..."
response=$(curl -s -X GET "https://graph.facebook.com/v21.0/$WHATSAPP_PHONE_ID" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN")
if echo "$response" | grep -q '"id"'; then
  echo "✅ Credentials are valid"
  echo "$response" | jq . 2>/dev/null || echo "$response"
else
  echo "❌ Invalid credentials or missing permissions"
  echo "Response: $response"
fi