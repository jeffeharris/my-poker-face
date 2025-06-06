# my-poker-face
A poker game with LLMs where you can define who you are playing against 
and have a conversation with them while you play.

**NEW**: Check out the [Rich CLI version](README_RICH_CLI.md) for a beautiful terminal interface with visual poker table, Unicode cards, and enhanced UI! Run with `python3 working_game.py`

## Key Features

- **AI-Powered Personalities**: Play against famous personalities (Gordon Ramsay, Eeyore, Batman, etc.) powered by OpenAI
- **Dynamic Personality System**: AI personalities now change during gameplay! Their traits (aggression, bluff tendency) adapt based on wins, losses, and game events
- **Multiple Interfaces**: Console, Web (Flask), and Rich CLI versions
- **Persistent Games**: Save and resume games with full state preservation
- **Immersive Experience**: Each AI player has unique speech patterns, physical gestures, and playing styles
- **Personality Elasticity** *(NEW)*: AI moods and traits dynamically respond to game events, creating more realistic opponents

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

Once the environment is configured and requirements installed you can run the application in multiple ways:

### run the Rich CLI version (NEW - Beautiful Terminal UI):

`python3 working_game.py`

This launches the new Rich terminal interface with visual poker table, Unicode cards, and enhanced UI. See [README_RICH_CLI.md](README_RICH_CLI.md) for details.

### run the console app locally:

`python -m console_app.ui_console`

### run the Flask app locally:

`python -m flask_app.ui_web`

And now you can access the local web app by going to http://127.0.0.1:5000
