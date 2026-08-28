#!/usr/bin/env bash
# =============================================================================
# WhatsApp Cloud API Test Token Helper
# This script guides you through obtaining a temporary test token from Meta.
# =============================================================================

# Step 1: Open the API testing page for your WhatsApp Business app
# Replace <APP_ID> with your actual app ID (found in the Facebook developer dashboard)
# Example URL: https://developers.facebook.com/apps/<APP_ID>/use_cases/customize/api-testing-v2/?use_case_enum=WHATSAPP_BUSINESS_MESSAGING&selected_tab=api-testing-v2&product_route=whatsapp-business
echo "📌 Open this URL in your browser (logged into the Facebook account that owns the app):"
echo "   https://developers.facebook.com/apps/<APP_ID>/use_cases/customize/api-testing-v2/?use_case_enum=WHATSAPP_BUSINESS_MESSAGING"
echo

# Step 2: Get a test phone number
echo "📱 On the page, find the section 'Get a test phone number for WhatsApp'."
echo "   Click the button 'Get Test Phone Number'."
echo "   Wait for the process to finish; you will see:"
echo "   - A test phone number (e.g. +1 555-123-4567)"
echo "   - An access token (string starting with 'EAAB...')"
echo "   - A phone-number ID (a long numeric string)"
echo

# Step 3: Add yourself as a verified recipient
echo "🔐 Under 'Manage phone number list', add your mobile number in international format"
echo "   (e.g. +919XXXXXXXX). You will receive an OTP via SMS or call to verify."
echo "   Once verified, the number can receive messages from the test number."
echo

# Step 4: (Optional) Send a test message
echo "💬 You can use the 'Send a test message' section on the same page to verify."
echo "   Select your verified number, keep the default template, and click Send."
echo "   You should receive a WhatsApp message from the test number."
echo

# Step 5: Store credentials in .env
echo "💾 Copy the two values below into your project's .env file:"
echo "   WHATSAPP_TOKEN=<the access token you copied>"
echo "   WHATSAPP_PHONE_ID=<the phone-number ID you copied>"
echo "   (Also set VERIFY_TOKEN to any string you like for webhook verification)"
echo

# Step 6: Verify the token works (run this script again after filling .env)
if [[ -n "$WHATSAPP_TOKEN" && -n "$WHATSAPP_PHONE_ID" ]]; then
  echo "🔍 Verifying credentials from environment..."
  response=$(curl -s -X GET "https://graph.facebook.com/v21.0/$WHATSAPP_PHONE_ID" \
    -H "Authorization: Bearer $WHATSAPP_TOKEN")
  if echo "$response" | grep -q '"id"'; then
    echo "✅ Credentials are valid!"
    echo "$response" | jq .
  else
    echo "❌ Invalid credentials or missing permissions."
    echo "Response: $response"
  fi
else
  echo "⚠️  Set WHATSAPP_TOKEN and WHATSAPP_PHONE_ID in .env to run verification."
fi