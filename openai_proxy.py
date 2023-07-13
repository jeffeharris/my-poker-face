# This is the OpenAI API proxy server. It needs to be running to actually send requests to OpenAI

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import requests
import os

# Load variables from the .env file
load_dotenv()

# Access the environment variable
openai_api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)
CORS(app)


@app.route('/api', methods=['POST'])
def api():
    headers = {
        'Authorization': f'Bearer {openai_api_key}',
        'Content-Type': 'application/json'
    }    
    
    response = requests.post('https://api.openai.com/v1/chat/completions', headers=headers, json=request.get_json())
    
    return jsonify(response.json()), response.status_code


if __name__ == '__main__':
    app.run(port=3000, debug=True)
