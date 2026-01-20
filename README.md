# My Poker Face

A poker game with AI personalities — and an experimentation platform for testing LLM capabilities at scale.

## The Game

Play Texas Hold'em against AI characters like Gordon Ramsay, Batman, and Eeyore. Each has unique speech patterns, playing styles, and dynamic behaviors that evolve during gameplay.

**Play now**: [mypokerfacegame.com](https://mypokerfacegame.com)

## The Experiment Manager

Run automated AI tournaments to compare models, test prompt variations, and analyze decision quality. Features include:

- **Multi-provider support**: OpenAI, Anthropic, Groq, DeepSeek, Mistral, Google, xAI
- **A/B testing**: Compare models head-to-head with deterministic seeding
- **Decision analysis**: Evaluate AI choices against optimal play (equity, EV)
- **Cost tracking**: Per-call usage logging with detailed breakdowns

## Quick Start

```bash
cp .env.example .env    # Add your API keys
make up                 # Start with Docker
```

Open [http://localhost:5173](http://localhost:5173)

## Documentation

- [Quick Start Guide](docs/QUICK_START.md) — Setup options and first game
- [Game Vision](docs/vision/GAME_VISION.md) — Design philosophy
- [DevOps Guide](docs/DEVOPS.md) — Production deployment
- [Troubleshooting](docs/TROUBLESHOOTING.md) — Common issues

## Development

```bash
# Run tests
docker compose exec backend python -m pytest tests/ -v

# View logs
make logs

# Stop services
make down
```
