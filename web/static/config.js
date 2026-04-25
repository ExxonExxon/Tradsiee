/**
 * Tradsiee Global Configuration
 * Centralizes environment-specific variables.
 */
const TRADSIEE_ENV = {
    // Detects if running locally or on production
    isLocal: window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1',
    
    // The base URL for API calls
    get API_BASE() {
        return this.isLocal ? "http://127.0.0.1:8000" : "https://tradsiee.com";
    }
};
