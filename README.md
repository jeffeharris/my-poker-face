# my-poker-face
A poker game with LLMs where you can define who you are playing against 
and have a conversation with them while you play.

## tech stack
My Poker Face uses Python, Flask, HTML, and JavaScript to host a web-based
browser game

## run the game locally
### optional: create a venv to run the game

- use the following commands to create a venv. first switch to the root 
directory of the project and then run the following. Depending on your 
python interpreter, you may need to switch 'python' to 'python3' below

`python -m venv my_poker_face_venv`

`source my_poker_face_venv/bin/activate`

`pip install -r requirements.txt`

### set up your `.env` file
Create a local `.env` file and add your `OPENAI_API_KEY` to it. 
This will be enabled to use the AI PLayers and Assistants.

### run the Flask app locally:
Once the environment is configured and the requirements installed you can
start the Flask application

`python ./flask_app/flask_app.py`

And now you can access the local web app by going to http://127.0.0.1:5000