# Running the Poker Game with Docker

## Prerequisites

1. Docker and Docker Compose installed
2. OpenAI API key

## Setup

1. **Create a `.env` file** in the project root:
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env`** and add your OpenAI API key:
   ```
   OPENAI_API_KEY=sk-your-actual-api-key-here
   ```

3. **Build and run the container**:
   ```bash
   docker compose up -d --build
   ```

4. **Access the game** at http://localhost:5000

## Troubleshooting

- If you see an OpenAI API error, make sure your `.env` file contains a valid API key
- The database is stored in the `data/` directory (created automatically)
- Logs: `docker compose logs -f`
- Restart: `docker compose restart`
- Stop: `docker compose down`

## Development

The container runs with hot-reload enabled, so code changes will automatically restart the Flask server.