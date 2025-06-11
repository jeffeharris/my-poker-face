# Minimal Flask app for Render deployment testing
import os
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'development-secret-key')
CORS(app, origins="*")

@app.route('/')
def index():
    return jsonify({
        'message': 'My Poker Face API',
        'status': 'healthy',
        'endpoints': {
            'health': '/health',
            'games': '/api/pokergame'
        }
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'poker-backend'
    })

@app.route('/api/pokergame')
def list_games():
    return jsonify({
        'games': [],
        'message': 'API is working'
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))