import os
import logging
from dotenv import load_dotenv
from supabase import Client, create_client
from twilio.rest import Client as TwilioClient
import cloudinary

# Load environment variables
load_dotenv()

# Observability Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tradsiee-engine")

# Environment Variables
API_BASE_URL = os.getenv("API_BASE_URL", "https://tradsiee.com")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://tradsiee.com")
LEAD_LIMITS_ENABLED = os.getenv("LEAD_LIMITS_ENABLED", "false").lower() == "true"

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TEFLON_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID")
TWILIO_VERIFY_SERVICE_SID = "VAedfe579dd3c049e952fd5db803f3fe56"
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", SUPABASE_SERVICE_KEY)

supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def get_supabase_user_client(token: str) -> Client:
    # Creates a Supabase client scoped to the specific user's JWT.
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.auth.set_session(token, "")
    return client

# Cloudinary Configuration
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

# Global Transient State
verification_codes = {}
sms_last_sent = {}
registration_attempts = {}
lead_submissions = {}

# Template Caches
WIDGET_TEMPLATE_CACHE = None
HTML_PAGES_CACHE = {}
